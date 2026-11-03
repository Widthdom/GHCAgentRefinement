import argparse
import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


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


def _compile_patterns(patterns, key):
    # 行全体に一致させる定義（`debugger` 文など）のため、行頭・行末は各行に一致させる。
    try:
        return [re.compile(pattern, re.MULTILINE) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {key}: {ex}') from None


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
    debug_patterns = _compile_patterns([definition['pattern'] for definition in debug_definitions], _DEBUG_PATTERNS_KEY)
    return (
        _compile_patterns(excluded, _EXCLUDED_FILE_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(debug_definitions, debug_patterns)],
    )


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・編集・本hookによる差し戻し済みかを集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    # 編集ツールの引数の解析は、共通utilityで行う。
    extract_edit_tool_changes = runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'extract-edit-tool-changes' / 'extract-edit-tool-changes.py')
    )['extract_edit_tool_changes']
    last_assistant_msg = None
    changes = []
    already_requested = False
    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects' / 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        event_type = event.get('type')

        if event_type == 'user.message':
            content = data.get('content')
            if data.get('delivery') != 'queued':
                last_assistant_msg = None
                changes.clear()
                already_requested = False
            elif isinstance(content, str) and content.startswith(_REASON_PREFIX):
                already_requested = True
        elif event_type == 'assistant.message':
            content = data.get('content')
            if isinstance(content, str) and content.strip():
                last_assistant_msg = content
        elif event_type == 'tool.execution_start':
            tool_name = data.get('toolName')
            if isinstance(tool_name, str):
                changes.extend(extract_edit_tool_changes(tool_name, data.get('arguments')))
    return last_assistant_msg, changes, already_requested


def _format_finding(finding):
    display_path, name, line = finding
    if len(line) > _MAX_SNIPPET_LENGTH:
        line = line[:_MAX_SNIPPET_LENGTH] + '...'
    return f'- {display_path}: {name} ("{line}")'


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug-leftover-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, debug_patterns = _load_definitions(parsed_arguments.definitions_path)
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
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event' / 'wait-for-agent-stop-event.py')
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
