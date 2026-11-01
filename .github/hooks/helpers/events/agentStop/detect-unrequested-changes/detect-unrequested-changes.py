import argparse
import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
OLD_TEXT_KEYS = ('old_str', 'old_string')
NEW_TEXT_KEYS = ('new_str', 'new_string')
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+?)\s*$')
PATCH_MOVE_PATTERN = re.compile(r'^\*\*\* Move to: (.+?)\s*$')
WHITESPACE_PATTERN = re.compile(r'\s+')
EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
FORMATTING_REQUEST_PATTERNS_KEY = 'formattingRequestPatterns'
REMOVAL_REQUEST_PATTERNS_KEY = 'removalRequestPatterns'
# 名前の一部（拡張子を除いた名前・親ディレクトリ名）で言及を判定するときの最小の長さ。短い名前の偶然の一致を避ける。
MIN_NAME_PART_LENGTH = 4
REASON_PREFIX = 'Some changes in this request are not clearly part of what the user asked for'
MAX_FINDINGS = 10
WHITESPACE_ONLY = 'whitespace-only change that the request did not ask for'
DELETED = 'deleted although the request did not ask to delete or move files'
MOVED = 'moved to {path} although the request did not ask to delete or move files'
UNMENTIONED = 'changed, but neither the request nor the responses mention this file'
BLOCK_REASON = (
    REASON_PREFIX + ':\n{findings}\n'
    'Check each one against the request. Revert changes that are outside it, such as unrelated reformatting, '
    'renames, deletions, or drive-by edits. If a change is needed for the request, keep it and say in the response '
    'which file changed and why, so the user can review it.'
)


def compile_patterns(patterns, key):
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {key}: {ex}') from None


def load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('unrequested change definitions must be a JSON object.')
    excluded = definitions.get(EXCLUDED_FILE_PATTERNS_KEY)
    if not isinstance(excluded, list) or not all(isinstance(item, str) and item.strip() for item in excluded):
        raise ValueError(
            f'unrequested change definitions require an array of non-empty strings for {EXCLUDED_FILE_PATTERNS_KEY}.'
        )
    patterns = [compile_patterns(excluded, EXCLUDED_FILE_PATTERNS_KEY)]
    for key in (FORMATTING_REQUEST_PATTERNS_KEY, REMOVAL_REQUEST_PATTERNS_KEY):
        value = definitions.get(key)
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f'unrequested change definitions require a non-empty array of non-empty strings for {key}.')
        patterns.append(compile_patterns(value, key))
    return tuple(patterns)


def collect_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item)


def is_whitespace_only(old, new):
    # 空白（インデント・改行・行末の空白）だけが異なる変更か。
    return old != new and bool(old.strip()) and WHITESPACE_PATTERN.sub('', old) == WHITESPACE_PATTERN.sub('', new)


def get_patch_changes(text):
    # パッチをファイルごとに分け、削除・移動と、追加行と削除行が空白だけ異なる変更を特定する。
    changes = []
    for line in text.splitlines():
        match = PATCH_FILE_PATTERN.match(line)
        move = PATCH_MOVE_PATTERN.match(line)
        if match:
            changes.append({'path': match.group(2), 'kind': match.group(1), 'moved_to': None, 'added': [], 'removed': []})
        elif changes and move:
            changes[-1]['moved_to'] = move.group(1)
        elif changes and line.startswith('+'):
            changes[-1]['added'].append(line[1:])
        elif changes and line.startswith('-'):
            changes[-1]['removed'].append(line[1:])
    return [
        {
            'path': change['path'],
            'deleted': change['kind'] == 'Delete',
            'moved_to': change['moved_to'],
            'whitespace_only': change['kind'] == 'Update' and is_whitespace_only(
                '\n'.join(change['removed']), '\n'.join(change['added']),
            ),
        }
        for change in changes
    ]


def get_changes(tool_name, arguments):
    # 戻り値は {path, deleted, moved_to（移動先またはNone）, whitespace_only} のリスト。
    if tool_name == 'apply_patch':
        return [change for text in collect_strings(arguments) for change in get_patch_changes(text)]
    if not isinstance(arguments, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and arguments.get('command') == 'view':
        return []
    path = arguments.get('path')
    if not isinstance(path, str) or not path.strip():
        return []
    old = next((arguments[key] for key in OLD_TEXT_KEYS if isinstance(arguments.get(key), str)), None)
    new = next((arguments[key] for key in NEW_TEXT_KEYS if isinstance(arguments.get(key), str)), None)
    return [{
        'path': path,
        'deleted': False,
        'moved_to': None,
        'whitespace_only': old is not None and new is not None and is_whitespace_only(old, new),
    }]


def get_arguments(raw_arguments):
    # argumentsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列（apply_patch）で記録される場合もある。
    if isinstance(raw_arguments, str):
        try:
            return json.loads(raw_arguments)
        except ValueError:
            return raw_arguments
    return raw_arguments


def get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、依頼の文・応答の文・変更・本hookによる差し戻し済みかを集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    # 割り込みで追加された指示も依頼に含める。
    request_texts = []
    response_texts = []
    changes = []
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
                request_texts.clear()
                response_texts.clear()
                changes.clear()
                already_requested = False
            if isinstance(content, str) and content.startswith(REASON_PREFIX):
                already_requested = True
            elif isinstance(content, str):
                request_texts.append(content)
        elif event_type == 'assistant.message':
            content = data.get('content')
            if isinstance(content, str) and content.strip():
                response_texts.append(content)
        elif event_type == 'tool.execution_start':
            tool_name = data.get('toolName')
            if isinstance(tool_name, str) and tool_name.removeprefix('functions.') in EDIT_TOOL_NAMES:
                changes.extend(get_changes(tool_name.removeprefix('functions.'), get_arguments(data.get('arguments'))))
    return '\n'.join(request_texts), '\n'.join(response_texts), changes, already_requested


def get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path


def is_mentioned(display_path, text):
    # ファイル名、または十分に長い拡張子を除いた名前・親ディレクトリ名が、依頼か応答にあれば言及したとみなす。
    # パスは `/` 区切りで照合し、大文字小文字は区別しない。
    parts = display_path.replace(os.sep, '/').split('/')
    name = parts[-1]
    candidates = [name.casefold()]
    stem = os.path.splitext(name)[0]
    if len(stem) >= MIN_NAME_PART_LENGTH:
        candidates.append(stem.casefold())
    if len(parts) > 1 and len(parts[-2]) >= MIN_NAME_PART_LENGTH:
        candidates.append(parts[-2].casefold())
    folded = text.casefold()
    return any(candidate in folded for candidate in candidates)


def find_unrequested_changes(changes, cwd, request_text, response_text, definitions):
    # 戻り値は (表示パス, 理由のリスト) のリスト。同じファイルの理由はまとめる。
    excluded_file_patterns, formatting_request_patterns, removal_request_patterns = definitions
    formatting_requested = any(pattern.search(request_text) for pattern in formatting_request_patterns)
    removal_requested = any(pattern.search(request_text) for pattern in removal_request_patterns)
    mention_text = '\n'.join([request_text, response_text])
    real_cwd = os.path.realpath(cwd)
    findings = {}
    for change in changes:
        display_path = get_display_path(os.path.realpath(os.path.join(cwd, change['path'])), real_cwd)
        if any(pattern.search(display_path) for pattern in excluded_file_patterns):
            continue
        reasons = findings.setdefault(display_path, [])
        if change['whitespace_only'] and not formatting_requested:
            reasons.append(WHITESPACE_ONLY)
        if not removal_requested:
            if change['deleted']:
                reasons.append(DELETED)
            elif change['moved_to']:
                moved_to = get_display_path(os.path.realpath(os.path.join(cwd, change['moved_to'])), real_cwd)
                reasons.append(MOVED.format(path=moved_to))
        if not is_mentioned(display_path, mention_text):
            reasons.append(UNMENTIONED)
    return [
        (display_path, list(dict.fromkeys(reasons))) for display_path, reasons in findings.items() if reasons
    ]


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'unrequested-change-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = load_definitions(parsed_arguments.definitions_path)
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

        request_text, response_text, changes, already_requested = get_turn_summary(transcript_path)
        # 同じ依頼で差し戻すのは1回だけにし、必要な変更を応答で説明した後に停止できなくなるのを防ぐ。
        if not response_text or already_requested or not changes:
            return 0
        findings = find_unrequested_changes(changes, cwd, request_text, response_text, definitions)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect unrequested changes: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = BLOCK_REASON.format(findings='\n'.join(
            f'- {display_path}: {"; ".join(reasons)}' for display_path, reasons in findings[:MAX_FINDINGS]
        ))
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
