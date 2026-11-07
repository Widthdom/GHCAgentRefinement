import argparse
import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']


_EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
_DEBUG_PATTERNS_KEY = 'debugPatterns'
_REASON_PREFIX = 'The changes in this request leave debugging code'
_MAX_FINDINGS = 10
_MAX_SNIPPET_LENGTH = 80
_BLOCK_REASON = (
    _REASON_PREFIX + ':\n{findings}\n'
    'Debug output and debugger calls added while investigating should not remain: they clutter output, can leak '
    'data, and a debugger call stops the program. Remove them now. If one is intended (for example, the output of a '
    'command-line tool or logging the user asked for), keep it and say so in the response.'
)


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('debug leftover definitions must be a JSON object.')
    excluded = definitions.get(_EXCLUDED_FILE_PATTERNS_KEY)
    if not isinstance(excluded, list) or not all(isinstance(item, str) and item.strip() for item in excluded):
        raise ValueError(
            f'debug leftover definitions require an array of non-empty strings for {_EXCLUDED_FILE_PATTERNS_KEY}.'
        )
    debug_definitions = definitions.get(_DEBUG_PATTERNS_KEY)
    if not isinstance(debug_definitions, list) or not debug_definitions or not all(
        isinstance(definition, dict) and all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        )
        for definition in debug_definitions
    ):
        raise ValueError(
            'debug leftover definitions require a non-empty array of objects with non-empty strings for name and '
            f'pattern in {_DEBUG_PATTERNS_KEY}.'
        )
    # 行全体に一致させる定義（`debugger` 文など）のため、行頭・行末は各行に一致させる。
    debug_patterns = _build_regex_matchers(
        [definition['pattern'] for definition in debug_definitions], _DEBUG_PATTERNS_KEY, re.MULTILINE,
    )
    return (
        _build_regex_matchers(excluded, _EXCLUDED_FILE_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(debug_definitions, debug_patterns)],
    )


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・編集・本hookによる差し戻し済みかを集める。
    # 編集ツールの引数の解析は、共通utilityで行う。
    extract_edit_tool_changes = runpy.run_path(
        os.path.join(_utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py')
    )['extract_edit_tool_changes']
    events, already_requested = _turn_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
    changes = [
        change
        for event_type, data in events
        if event_type == 'tool.execution_start' and isinstance(data.get('toolName'), str)
        for change in extract_edit_tool_changes(data['toolName'], data.get('arguments'))
    ]
    return _turn_events['get_last_assistant_message'](events), changes, already_requested


def _format_finding(finding):
    display_path, name, line = finding
    if len(line) > _MAX_SNIPPET_LENGTH:
        line = line[:_MAX_SNIPPET_LENGTH] + '...'
    return f'- {display_path}: {name} ("{line}")'


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug-leftover-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, debug_patterns = _load_definitions(parsed_arguments.definitions_path)
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

        last_assistant_msg, changes, already_requested = _get_turn_summary(transcript_path)
        # 同じ依頼で差し戻すのは1回だけにし、意図して残す場合に停止できなくなるのを防ぐ。
        if last_assistant_msg is None or already_requested or not changes:
            return 0
        # 新たに持ち込んだ行の照合は、共通utilityで行う。
        find_introduced_lines_by_regex = runpy.run_path(str(
            Path(__file__).resolve().parents[3] / 'utilities' / 'find-introduced-lines-by-regex'
            / 'find-introduced-lines-by-regex.py'
        ))['find_introduced_lines_by_regex']
        findings = find_introduced_lines_by_regex(changes, cwd, excluded_file_patterns, debug_patterns)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect debug leftovers: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = _BLOCK_REASON.format(findings='\n'.join(_format_finding(finding) for finding in findings[:_MAX_FINDINGS]))
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
