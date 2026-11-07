import argparse
import io
import json
import os
import re
import runpy
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']


_WHITESPACE_PATTERN = re.compile(r'\s+')
_EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
_FORMATTING_REQUEST_PATTERNS_KEY = 'formattingRequestPatterns'
_REMOVAL_REQUEST_PATTERNS_KEY = 'removalRequestPatterns'
# 名前の一部（拡張子を除いた名前・親ディレクトリ名）で言及を判定するときの最小の長さ。短い名前の偶然の一致を避ける。
_MIN_NAME_PART_LENGTH = 4
_REASON_PREFIX = 'Some changes in this request are not clearly part of what the user asked for'
_MAX_FINDINGS = 10
_WHITESPACE_ONLY = 'whitespace-only change that the request did not ask for'
_DELETED = 'deleted although the request did not ask to delete or move files'
_MOVED = 'moved to {path} although the request did not ask to delete or move files'
_UNMENTIONED = 'changed, but neither the request nor the responses mention this file'
_BLOCK_REASON = (
    _REASON_PREFIX + ':\n{findings}\n'
    'Check each one against the request. Revert changes that are outside it, such as unrelated reformatting, '
    'renames, deletions, or drive-by edits. If a change is needed for the request, keep it and say in the response '
    'which file changed and why, so the user can review it.'
)


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('unrequested change definitions must be a JSON object.')
    excluded = definitions.get(_EXCLUDED_FILE_PATTERNS_KEY)
    if not isinstance(excluded, list) or not all(isinstance(item, str) and item.strip() for item in excluded):
        raise ValueError(
            f'unrequested change definitions require an array of non-empty strings for {_EXCLUDED_FILE_PATTERNS_KEY}.'
        )
    patterns = [_build_regex_matchers(excluded, _EXCLUDED_FILE_PATTERNS_KEY)]
    for key in (_FORMATTING_REQUEST_PATTERNS_KEY, _REMOVAL_REQUEST_PATTERNS_KEY):
        value = definitions.get(key)
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f'unrequested change definitions require a non-empty array of non-empty strings for {key}.')
        patterns.append(_build_regex_matchers(value, key))
    return tuple(patterns)


def _is_whitespace_only(old, new):
    # 空白（インデント・改行・行末の空白）だけが異なる変更か。
    return old != new and bool(old.strip()) and _WHITESPACE_PATTERN.sub('', old) == _WHITESPACE_PATTERN.sub('', new)


def _is_whitespace_only_change(change):
    # パッチでは削除行と追加行、置換では置換前後の内容を比べる。作成・挿入・削除は対象外。
    if change['patch_lines'] is not None:
        return _is_whitespace_only(change['removed'], change['written'])
    return change['replacement'] is not None and _is_whitespace_only(*change['replacement'])


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、依頼の文・応答の文・変更・本hookによる差し戻し済みかを集める。
    # 割り込みで追加された指示も依頼に含める。
    # 変更は {path, deleted, moved_to（移動先またはNone）, whitespace_only} のリスト。
    # 編集ツールの引数の解析は、共通utilityで行う。
    extract_edit_tool_changes = runpy.run_path(
        os.path.join(_utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py')
    )['extract_edit_tool_changes']
    events, already_requested = _turn_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
    request_texts = []
    response_texts = []
    changes = []
    started = {}
    for event_type, data in events:
        content = data.get('content')
        if event_type == 'user.message':
            if isinstance(content, str) and not content.startswith(_REASON_PREFIX):
                request_texts.append(content)
        elif event_type == 'assistant.message':
            if isinstance(content, str) and content.strip():
                response_texts.append(content)
        elif event_type == 'tool.execution_start':
            call_id = data.get('toolCallId')
            tool_name = data.get('toolName')
            if isinstance(call_id, str) and isinstance(tool_name, str):
                started[call_id] = (tool_name, data.get('arguments'))
        elif event_type == 'tool.execution_complete':
            call_id = data.get('toolCallId')
            if not isinstance(call_id, str) or call_id not in started:
                continue
            tool_name, arguments = started.pop(call_id)
            if data.get('success') is True:
                changes.extend(
                    {
                        'path': change['path'],
                        'deleted': change['deleted'],
                        'moved_to': change['moved_to'],
                        'whitespace_only': _is_whitespace_only_change(change),
                    }
                    for change in extract_edit_tool_changes(tool_name, arguments, include_deleted=True)
                )
    return '\n'.join(request_texts), '\n'.join(response_texts), changes, already_requested


def _is_mentioned(display_path, text):
    # ファイル名、または十分に長い拡張子を除いた名前・親ディレクトリ名が、依頼か応答にあれば言及したとみなす。
    # パスは `/` 区切りで照合し、大文字小文字は区別しない。
    parts = display_path.split('/')
    name = parts[-1]
    candidates = [name.casefold()]
    stem = os.path.splitext(name)[0]
    if len(stem) >= _MIN_NAME_PART_LENGTH:
        candidates.append(stem.casefold())
    if len(parts) > 1 and len(parts[-2]) >= _MIN_NAME_PART_LENGTH:
        candidates.append(parts[-2].casefold())
    folded = text.casefold()
    return any(candidate in folded for candidate in candidates)


def _find_unrequested_changes(changes, cwd, request_text, response_text, definitions):
    # 戻り値は (表示パス, 理由のリスト) のリスト。同じファイルの理由はまとめる。
    excluded_file_patterns, formatting_request_patterns, removal_request_patterns = definitions
    formatting_requested = any(pattern.search(request_text) for pattern in formatting_request_patterns)
    removal_requested = any(pattern.search(request_text) for pattern in removal_request_patterns)
    mention_text = '\n'.join([request_text, response_text])
    real_cwd = os.path.realpath(cwd)
    findings = {}
    for change in changes:
        display_path = _get_display_path(os.path.realpath(os.path.join(cwd, change['path'])), real_cwd)
        if any(pattern.search(display_path) for pattern in excluded_file_patterns):
            continue
        reasons = findings.setdefault(display_path, [])
        if change['whitespace_only'] and not formatting_requested:
            reasons.append(_WHITESPACE_ONLY)
        if not removal_requested:
            if change['deleted']:
                reasons.append(_DELETED)
            elif change['moved_to']:
                moved_to = _get_display_path(os.path.realpath(os.path.join(cwd, change['moved_to'])), real_cwd)
                reasons.append(_MOVED.format(path=moved_to))
        if not _is_mentioned(display_path, mention_text):
            reasons.append(_UNMENTIONED)
    return [
        (display_path, list(dict.fromkeys(reasons))) for display_path, reasons in findings.items() if reasons
    ]


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'unrequested-change-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = _load_definitions(parsed_arguments.definitions_path)
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

        request_text, response_text, changes, already_requested = _get_turn_summary(transcript_path)
        # 同じ依頼で差し戻すのは1回だけにし、必要な変更を応答で説明した後に停止できなくなるのを防ぐ。
        if not response_text or already_requested or not changes:
            return 0
        findings = _find_unrequested_changes(changes, cwd, request_text, response_text, definitions)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect unrequested changes: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = _BLOCK_REASON.format(findings='\n'.join(
            f'- {display_path}: {"; ".join(reasons)}' for display_path, reasons in findings[:_MAX_FINDINGS]
        ))
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
