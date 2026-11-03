import argparse
import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
DEBUG_PATTERNS_KEY = 'debugPatterns'
REASON_PREFIX = 'The changes in this request leave debugging code'
MAX_FINDINGS = 10
MAX_SNIPPET_LENGTH = 80
BLOCK_REASON = (
    REASON_PREFIX + ':\n{findings}\n'
    'Debug output and debugger calls added while investigating should not remain: they clutter output, can leak '
    'data, and a debugger call stops the program. Remove them now. If one is intended (for example, the output of a '
    'command-line tool or logging the user asked for), keep it and say so in the response.'
)


def compile_patterns(patterns, key):
    # 行全体に一致させる定義（`debugger` 文など）のため、行頭・行末は各行に一致させる。
    try:
        return [re.compile(pattern, re.MULTILINE) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {key}: {ex}') from None


def load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('debug leftover definitions must be a JSON object.')
    excluded = definitions.get(EXCLUDED_FILE_PATTERNS_KEY)
    if not isinstance(excluded, list) or not all(isinstance(item, str) and item.strip() for item in excluded):
        raise ValueError(
            f'debug leftover definitions require an array of non-empty strings for {EXCLUDED_FILE_PATTERNS_KEY}.'
        )
    debug_definitions = definitions.get(DEBUG_PATTERNS_KEY)
    if not isinstance(debug_definitions, list) or not debug_definitions or not all(
        isinstance(definition, dict) and all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        )
        for definition in debug_definitions
    ):
        raise ValueError(
            'debug leftover definitions require a non-empty array of objects with non-empty strings for name and '
            f'pattern in {DEBUG_PATTERNS_KEY}.'
        )
    debug_patterns = compile_patterns([definition['pattern'] for definition in debug_definitions], DEBUG_PATTERNS_KEY)
    return (
        compile_patterns(excluded, EXCLUDED_FILE_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(debug_definitions, debug_patterns)],
    )


def get_turn_summary(transcript_path):
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
            elif isinstance(content, str) and content.startswith(REASON_PREFIX):
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


def normalize_lines(text):
    # インデントの違いで既存の内容との照合が外れないよう、各行の前後の空白を除く。
    return {line.strip() for line in text.splitlines()}


def read_lines(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return normalize_lines(file.read())
    except OSError:
        return None


def get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path


def find_debug_leftovers(changes, cwd, excluded_file_patterns, debug_patterns):
    # 戻り値は (表示パス, 種類, 該当行) のリスト。
    # 置換前の内容に同じ行があるもの（元からあるもの）と、最終的にファイルから消えたものは除く。
    findings = []
    current_lines = {}
    real_cwd = os.path.realpath(cwd)
    for change in changes:
        path, written, removed = change['path'], change['written'], change['removed']
        full_path = os.path.realpath(os.path.join(cwd, path))
        # パターンの照合と理由での表示は、OSによらず `/` 区切りのパスで行う。
        display_path = get_display_path(full_path, real_cwd).replace(os.sep, '/')
        if any(pattern.search(display_path) for pattern in excluded_file_patterns):
            continue
        removed_lines = normalize_lines(removed)
        for name, pattern in debug_patterns:
            for match in pattern.finditer(written):
                start = written.rfind('\n', 0, match.start()) + 1
                end = written.find('\n', match.start())
                line = written[start:None if end < 0 else end].strip()
                if line in removed_lines or any(finding[2] == line for finding in findings):
                    continue
                if full_path not in current_lines:
                    current_lines[full_path] = read_lines(full_path) if os.path.isfile(full_path) else None
                lines = current_lines[full_path]
                if lines is not None and line in lines:
                    findings.append((display_path, name, line))
    return findings


def format_finding(finding):
    display_path, name, line = finding
    if len(line) > MAX_SNIPPET_LENGTH:
        line = line[:MAX_SNIPPET_LENGTH] + '...'
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
        excluded_file_patterns, debug_patterns = load_definitions(parsed_arguments.definitions_path)
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

        last_assistant_msg, changes, already_requested = get_turn_summary(transcript_path)
        # 同じ依頼で差し戻すのは1回だけにし、意図して残す場合に停止できなくなるのを防ぐ。
        if last_assistant_msg is None or already_requested or not changes:
            return 0
        findings = find_debug_leftovers(changes, cwd, excluded_file_patterns, debug_patterns)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect debug leftovers: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = BLOCK_REASON.format(findings='\n'.join(format_finding(finding) for finding in findings[:MAX_FINDINGS]))
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
