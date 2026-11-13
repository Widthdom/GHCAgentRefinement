import argparse
import io
import json
import os
import runpy
import sys
import tomllib
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))
_extract_edit_tool_changes = runpy.run_path(os.path.join(
    _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
))['extract_edit_tool_changes']


_FORMATS = {'json', 'toml', 'lines'}
_GIT_TIMEOUT_SECONDS = 5
_MAX_FINDINGS = 10
_REASON_PREFIX = 'The changes in this request edit dependency declarations without updating the lock file'
_BLOCK_REASON = (
    _REASON_PREFIX + ':\n{findings}\n'
    'A lock file that no longer matches its manifest makes restores fail in frozen or locked mode (CI), or '
    'silently resolves different versions than the ones that were tested. Run the listed command to update the '
    'lock file and include the result, or revert the dependency change. If the lock file is intentionally left '
    'unchanged, say so and why in the final response.'
)


def _load_definitions(path):
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
        if manifest.get('format') not in _FORMATS:
            raise ValueError(f'manifest {name} requires format to be one of {sorted(_FORMATS)}.')
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
        file_pattern, *line_patterns = _build_regex_matchers(
            [manifest.get('filePattern') if isinstance(manifest.get('filePattern'), str) else '',
             *(values if manifest['format'] == 'lines' else [])],
            f'manifest {name}',
        )
        if not file_pattern.pattern:
            raise ValueError(f'manifest {name} requires a non-empty filePattern.')
        loaded.append({
            'name': name, 'file': file_pattern, 'format': manifest['format'],
            'keys': [] if line_patterns else values, 'lines': line_patterns, 'locks': locks,
        })
    return loaded


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・書き込んだファイル・本hookによる差し戻し済みかを集める。
    events, already_requested = _turn_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
    paths = []
    for event_type, data in events:
        tool_name = data.get('toolName')
        if event_type == 'tool.execution_start' and isinstance(tool_name, str):
            # 書き込むファイルと移動先を集める。削除するファイルは対象外とする。
            paths.extend(
                path for change in _extract_edit_tool_changes(tool_name, data.get('arguments'))
                for path in (change['path'], change['moved_to']) if path
            )
    return _turn_events['get_last_assistant_message'](events), paths, already_requested


def _resolve_keys(value, parts):
    # ドット区切りのキーを辿る。`*` はその階層のすべてのキーに一致する。戻り値は (キー, 値) のリスト。
    if not parts:
        return [((), value)]
    if not isinstance(value, dict):
        return []
    head, rest = parts[0], parts[1:]
    keys = sorted(value) if head == '*' else ([head] if head in value else [])
    return [((key, *path), found) for key in keys for path, found in _resolve_keys(value[key], rest)]


def _get_fingerprint(manifest, text):
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
        for path, value in _resolve_keys(document, key.split('.')):
            found['.'.join(path)] = value
    return json.dumps(found, ensure_ascii=False, sort_keys=True, default=str)


def _find_drifts(manifests, paths, cwd, repository_root):
    # 戻り値は (マニフェストの相対パス, [(ロックファイルの相対パス, コマンド)]) のリスト。
    drifts = []
    seen = set()
    for raw_path in paths:
        path = os.path.realpath(os.path.join(cwd, raw_path))
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            # 大文字小文字を区別しないWindowsでも配下と判定できるよう、normcaseで比べる。
            if os.path.normcase(os.path.commonpath([repository_root, path])) != os.path.normcase(repository_root):
                continue
        except ValueError:
            continue
        manifest = next((manifest for manifest in manifests if manifest['file'].search(os.path.basename(path))), None)
        if manifest is None:
            continue
        relative_path = os.path.relpath(path, repository_root).replace(os.sep, '/')
        # 編集前の内容はHEADのものとする。HEADにないマニフェスト（新規作成）は比べる対象がないため判定しない。
        head = _git['run_git'](repository_root, ['show', f'HEAD:{relative_path}'], _GIT_TIMEOUT_SECONDS)
        if head is None or head.returncode != 0:
            continue
        with open(path, encoding='utf-8-sig', errors='replace') as file:
            current = file.read()
        before = _get_fingerprint(manifest, head.stdout.decode('utf-8-sig', errors='replace'))
        after = _get_fingerprint(manifest, current)
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
            tracked = _git['run_git'](repository_root, ['ls-files', '--', lock_relative_path], _GIT_TIMEOUT_SECONDS)
            changed = _git['run_git'](repository_root, ['diff', '--quiet', 'HEAD', '--', lock_relative_path], _GIT_TIMEOUT_SECONDS)
            if tracked is None or not tracked.stdout.strip() or changed is None or changed.returncode != 0:
                break
        else:
            drifts.append((relative_path, locks))
    return drifts


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lockfile-drift-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        manifests = _load_definitions(parsed_arguments.definitions_path)
        hook_input = _hook_input['read_hook_input']()

        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0

        cwd = _hook_input['get_cwd'](hook_input)
        transcript_path = _hook_input['get_transcript_path'](hook_input)

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答と編集を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, paths, already_requested = _get_turn_summary(transcript_path)
        # 同じ依頼で差し戻すのは1回だけにし、意図して残す場合に停止できなくなるのを防ぐ。
        if last_assistant_msg is None or already_requested or not paths:
            return 0
        # HEADと比べるため、gitがない、またはリポジトリ外の場合は検査しない。
        repository_root = _git['get_repository_root'](cwd, _GIT_TIMEOUT_SECONDS)
        if repository_root is None:
            return 0
        drifts = _find_drifts(manifests, paths, cwd, os.path.realpath(repository_root))
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect lockfile drift: {ex}', file=sys.stderr)
        return 2

    if drifts:
        findings = '\n'.join(
            f'- {manifest}: {", ".join(lock for lock, _ in locks)} unchanged; run `{locks[0][1]}`'
            for manifest, locks in drifts[:_MAX_FINDINGS]
        )
        _hook_output['write_block_decision'](_BLOCK_REASON.format(findings=findings))
    return 0


if __name__ == '__main__':
    sys.exit(main())
