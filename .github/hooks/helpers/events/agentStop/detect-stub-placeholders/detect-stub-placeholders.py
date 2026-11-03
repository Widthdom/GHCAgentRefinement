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
PLACEHOLDER_PATTERNS_KEY = 'placeholderPatterns'
DISCLOSURE_PATTERNS_KEY = 'disclosurePatterns'
REASON_PREFIX = 'The changes in this request leave placeholder code'
MAX_FINDINGS = 10
MAX_SNIPPET_LENGTH = 80
BLOCK_REASON = (
    REASON_PREFIX + ' that the final response does not mention:\n{findings}\n'
    'Implement these parts now. If leaving them is intended (for example, an abstract method or work the user '
    'deferred), state in the response which parts remain unimplemented and why. Do not report the work as complete '
    'while placeholders remain.'
)


def compile_patterns(patterns, key):
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {key}: {ex}') from None


def is_string_list(value):
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)


def load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('stub placeholder definitions must be a JSON object.')
    for key in (EXCLUDED_FILE_PATTERNS_KEY, DISCLOSURE_PATTERNS_KEY):
        if not is_string_list(definitions.get(key)):
            raise ValueError(f'stub placeholder definitions require a non-empty array of non-empty strings for {key}.')
    placeholder_definitions = definitions.get(PLACEHOLDER_PATTERNS_KEY)
    if not isinstance(placeholder_definitions, list) or not placeholder_definitions or not all(
        isinstance(definition, dict) and all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        )
        for definition in placeholder_definitions
    ):
        raise ValueError(
            'stub placeholder definitions require a non-empty array of objects with non-empty strings for name and '
            f'pattern in {PLACEHOLDER_PATTERNS_KEY}.'
        )
    placeholder_patterns = compile_patterns(
        [definition['pattern'] for definition in placeholder_definitions], PLACEHOLDER_PATTERNS_KEY,
    )
    return (
        compile_patterns(definitions[EXCLUDED_FILE_PATTERNS_KEY], EXCLUDED_FILE_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(placeholder_definitions, placeholder_patterns)],
        compile_patterns(definitions[DISCLOSURE_PATTERNS_KEY], DISCLOSURE_PATTERNS_KEY),
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


def find_placeholders(changes, cwd, excluded_file_patterns, placeholder_patterns):
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
        for name, pattern in placeholder_patterns:
            for match in pattern.finditer(written):
                start = written.rfind('\n', 0, match.start()) + 1
                end = written.find('\n', match.start())
                line = written[start:None if end < 0 else end].strip()
                if line in removed_lines:
                    continue
                if full_path not in current_lines:
                    current_lines[full_path] = read_lines(full_path) if os.path.isfile(full_path) else None
                lines = current_lines[full_path]
                finding = (display_path, name, line)
                if lines is not None and line in lines and finding not in findings:
                    findings.append(finding)
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
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stub-placeholder-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, placeholder_patterns, disclosure_patterns = load_definitions(
            parsed_arguments.definitions_path
        )
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
        # 応答で未実装の箇所に触れていれば、ユーザーに伝わっているとみなす。
        if any(pattern.search(last_assistant_msg) for pattern in disclosure_patterns):
            return 0
        findings = find_placeholders(changes, cwd, excluded_file_patterns, placeholder_patterns)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect stub placeholders: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = BLOCK_REASON.format(findings='\n'.join(format_finding(finding) for finding in findings[:MAX_FINDINGS]))
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
