import argparse
import io
import json
import os
import re
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:Add|Update) File: (.+?)\s*$')
PATCH_MOVE_PATTERN = re.compile(r'^\*\*\* Move to: (.+?)\s*$')
FORMATS = {'json', 'toml', 'lines'}
GIT_TIMEOUT_SECONDS = 5
MAX_FINDINGS = 10
REASON_PREFIX = 'The changes in this request edit dependency declarations without updating the lock file'
BLOCK_REASON = (
    REASON_PREFIX + ':\n{findings}\n'
    'A lock file that no longer matches its manifest makes restores fail in frozen or locked mode (CI), or '
    'silently resolves different versions than the ones that were tested. Run the listed command to update the '
    'lock file and include the result, or revert the dependency change. If the lock file is intentionally left '
    'unchanged, say so and why in the final response.'
)


def load_definitions(path):
    # 戻り値は [{name, file, format, keys, lines, locks}]。正規表現はコンパイル済み。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    manifests = definitions.get('manifests') if isinstance(definitions, dict) else None
    if not isinstance(manifests, list) or not manifests:
        raise ValueError('lockfile drift definitions require a non-empty array for manifests.')
    loaded = []
    for manifest in manifests:
        if not isinstance(manifest, dict) or not isinstance(manifest.get('name'), str) or not manifest['name'].strip():
            raise ValueError('each manifest requires a non-empty name.')
        name = manifest['name']
        if manifest.get('format') not in FORMATS:
            raise ValueError(f'manifest {name} requires format to be one of {sorted(FORMATS)}.')
        locks = manifest.get('lockFiles')
        if not isinstance(locks, list) or not locks or not all(
            isinstance(lock, dict) and isinstance(lock.get('file'), str) and lock['file'].strip()
            and isinstance(lock.get('command'), str) and lock['command'].strip()
            for lock in locks
        ):
            raise ValueError(f'manifest {name} requires a non-empty array of {{file, command}} for lockFiles.')
        key = 'linePatterns' if manifest['format'] == 'lines' else 'dependencyKeys'
        values = manifest.get(key)
        if not isinstance(values, list) or not values or not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError(f'manifest {name} requires a non-empty array of non-empty strings for {key}.')
        try:
            file_pattern = re.compile(manifest.get('filePattern') if isinstance(manifest.get('filePattern'), str) else '')
            line_patterns = [re.compile(value) for value in values] if manifest['format'] == 'lines' else []
        except re.PatternError as ex:
            raise ValueError(f'invalid regular expression in manifest {name}: {ex}') from None
        if not file_pattern.pattern:
            raise ValueError(f'manifest {name} requires a non-empty filePattern.')
        loaded.append({
            'name': name, 'file': file_pattern, 'format': manifest['format'],
            'keys': [] if line_patterns else values, 'lines': line_patterns, 'locks': locks,
        })
    return loaded


def collect_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item)


def get_arguments(raw_arguments):
    # argumentsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列（apply_patch）で記録される場合もある。
    if isinstance(raw_arguments, str):
        try:
            return json.loads(raw_arguments)
        except ValueError:
            return raw_arguments
    return raw_arguments


def get_written_paths(tool_name, arguments):
    # 戻り値は、編集系ツールが書き込むファイルのパスのリスト。削除するファイルは対象外とする。
    if tool_name == 'apply_patch':
        return [
            match.group(1)
            for text in collect_strings(arguments) for line in text.splitlines()
            if (match := PATCH_FILE_PATTERN.match(line) or PATCH_MOVE_PATTERN.match(line))
        ]
    if not isinstance(arguments, dict) or arguments.get('command') in ('view', 'undo_edit'):
        return []
    path = arguments.get('path')
    return [path] if isinstance(path, str) and path.strip() else []


def get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・書き込んだファイル・本hookによる差し戻し済みかを集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    last_assistant_msg = None
    paths = []
    already_requested = False
    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        event_type = event.get('type')

        if event_type == 'user.message':
            content = data.get('content')
            if data.get('delivery') != 'queued':
                last_assistant_msg = None
                paths.clear()
                already_requested = False
            elif isinstance(content, str) and content.startswith(REASON_PREFIX):
                already_requested = True
        elif event_type == 'assistant.message':
            content = data.get('content')
            if isinstance(content, str) and content.strip():
                last_assistant_msg = content
        elif event_type == 'tool.execution_start':
            tool_name = data.get('toolName')
            if isinstance(tool_name, str) and tool_name.removeprefix('functions.') in EDIT_TOOL_NAMES:
                paths.extend(get_written_paths(tool_name.removeprefix('functions.'), get_arguments(data.get('arguments'))))
    return last_assistant_msg, paths, already_requested


def run_git(cwd, arguments):
    # 戻り値は (終了コード, 標準出力のバイト列)。gitがない、またはタイムアウトした場合はNone。
    try:
        result = subprocess.run(
            ['git', *arguments], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.returncode, result.stdout


def resolve_keys(value, parts):
    # ドット区切りのキーを辿る。`*` はその階層のすべてのキーに一致する。戻り値は (キー, 値) のリスト。
    if not parts:
        return [((), value)]
    if not isinstance(value, dict):
        return []
    head, rest = parts[0], parts[1:]
    keys = sorted(value) if head == '*' else ([head] if head in value else [])
    return [((key, *path), found) for key in keys for path, found in resolve_keys(value[key], rest)]


def get_fingerprint(manifest, text):
    # 依存の宣言だけを取り出し、比較できる文字列にする。読めない内容はNone（判定しない）。
    if manifest['format'] == 'lines':
        lines = sorted(
            ' '.join(match.group(0).split())
            for line in text.splitlines() for pattern in manifest['lines'] if (match := pattern.search(line))
        )
        return json.dumps(lines, ensure_ascii=False)
    try:
        document = json.loads(text) if manifest['format'] == 'json' else tomllib.loads(text)
    except ValueError:
        return None
    found = {}
    for key in manifest['keys']:
        for path, value in resolve_keys(document, key.split('.')):
            found['.'.join(path)] = value
    return json.dumps(found, ensure_ascii=False, sort_keys=True, default=str)


def find_drifts(manifests, paths, cwd, repository_root):
    # 戻り値は (マニフェストの相対パス, [(ロックファイルの相対パス, コマンド)]) のリスト。
    drifts = []
    seen = set()
    for raw_path in paths:
        path = os.path.realpath(os.path.join(cwd, raw_path))
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            if os.path.commonpath([repository_root, path]) != repository_root:
                continue
        except ValueError:
            continue
        manifest = next((manifest for manifest in manifests if manifest['file'].search(os.path.basename(path))), None)
        if manifest is None:
            continue
        relative_path = os.path.relpath(path, repository_root).replace(os.sep, '/')
        # 編集前の内容はHEADのものとする。HEADにないマニフェスト（新規作成）は比べる対象がないため判定しない。
        head = run_git(repository_root, ['show', f'HEAD:{relative_path}'])
        if head is None or head[0] != 0:
            continue
        with open(path, encoding='utf-8-sig', errors='replace') as file:
            current = file.read()
        before = get_fingerprint(manifest, head[1].decode('utf-8-sig', errors='replace'))
        after = get_fingerprint(manifest, current)
        if before is None or after is None or before == after:
            continue
        # ロックファイルは同じディレクトリにあるものだけを見る。1つもなければロックファイルを使わない構成とみなす。
        locks = [
            (os.path.relpath(lock_path, repository_root).replace(os.sep, '/'), lock['command'])
            for lock in manifest['locks']
            if os.path.isfile(lock_path := os.path.join(os.path.dirname(path), lock['file']))
        ]
        if not locks:
            continue
        for lock_relative_path, _ in locks:
            # git管理下でないロックファイルは新たに作ったものとみなし、HEADから変わったものとあわせて更新済みとする。
            tracked = run_git(repository_root, ['ls-files', '--', lock_relative_path])
            changed = run_git(repository_root, ['diff', '--quiet', 'HEAD', '--', lock_relative_path])
            if tracked is None or not tracked[1].strip() or changed is None or changed[0] != 0:
                break
        else:
            drifts.append((relative_path, locks))
    return drifts


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lockfile-drift-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        manifests = load_definitions(parsed_arguments.definitions_path)
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('agentStop hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('agentStop hook input must be a JSON object.')

        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0

        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd must be a non-empty string.')
        if not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        transcript_path = hook_input.get('transcriptPath')
        if not isinstance(transcript_path, str) or not transcript_path.strip():
            raise ValueError('transcriptPath must be a non-empty string.')
        if not os.path.isfile(transcript_path):
            raise ValueError(f'transcript path is not a file or could not be verified as a file: {transcript_path}')

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答と編集を書き込み済みにする。
        runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, paths, already_requested = get_turn_summary(transcript_path)
        # 同じ依頼で差し戻すのは1回だけにし、意図して残す場合に停止できなくなるのを防ぐ。
        if last_assistant_msg is None or already_requested or not paths:
            return 0
        # HEADと比べるため、gitがない、またはリポジトリ外の場合は検査しない。
        repository_root = run_git(cwd, ['rev-parse', '--show-toplevel'])
        if repository_root is None or repository_root[0] != 0 or not repository_root[1].strip():
            return 0
        drifts = find_drifts(
            manifests, paths, cwd, os.path.realpath(repository_root[1].decode('utf-8', errors='replace').strip()),
        )
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect lockfile drift: {ex}', file=sys.stderr)
        return 2

    if drifts:
        findings = '\n'.join(
            f'- {manifest}: {", ".join(lock for lock, _ in locks)} unchanged; run `{locks[0][1]}`'
            for manifest, locks in drifts[:MAX_FINDINGS]
        )
        print(json.dumps(
            {'decision': 'block', 'reason': BLOCK_REASON.format(findings=findings)},
            ensure_ascii=False, separators=(',', ':'),
        ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
