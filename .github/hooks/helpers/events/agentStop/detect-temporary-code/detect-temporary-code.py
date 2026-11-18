import argparse
import io
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
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_truncate_text = runpy.run_path(os.path.join(_utilities_directory, 'truncate-text', 'truncate-text.py'))['truncate_text']
_transcript_events = runpy.run_path(os.path.join(_utilities_directory, 'read-transcript-events', 'read-transcript-events.py'))
_definitions = runpy.run_path(os.path.join(_utilities_directory, 'read-definitions', 'read-definitions.py'))


_EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
_DEBUG_PATTERNS_KEY = 'debugPatterns'
_PLACEHOLDER_PATTERNS_KEY = 'placeholderPatterns'
_PLACEHOLDER_DISCLOSURE_PATTERNS_KEY = 'placeholderDisclosurePatterns'
# 差し戻しの理由の書き出しは種類ごとに変え、同じ依頼での差し戻しを種類ごとに1回に限る。
_DEBUG_PREFIX = 'The changes in this request leave debugging code'
_PLACEHOLDER_PREFIX = 'The changes in this request leave placeholder code'
_DEBUG_REASON = (
    _DEBUG_PREFIX + ':\n{findings}\n'
    'Debug output and debugger calls added while investigating should not remain: they clutter output, can leak '
    'data, and a debugger call stops the program. Remove them now. If one is intended (for example, the output of a '
    'command-line tool or logging the user asked for), keep it and say so in the response.'
)
_PLACEHOLDER_REASON = (
    _PLACEHOLDER_PREFIX + ' that the final response does not mention:\n{findings}\n'
    'Implement these parts now. If leaving them is intended (for example, an abstract method or work the user '
    'deferred), state in the response which parts remain unimplemented and why. Do not report the work as complete '
    'while placeholders remain.'
)
_MAX_FINDINGS = 10
_MAX_SNIPPET_LENGTH = 80


def _load_definitions(path):
    label = 'temporary code definitions'
    definitions = _definitions['read_definitions'](path, label)
    # 行全体に一致させる定義（`debugger` 文など）のため、行頭・行末は各行に一致させる。
    return (
        _definitions['get_regex_matchers'](definitions, _EXCLUDED_FILE_PATTERNS_KEY, label, allow_empty_list=True),
        _definitions['get_named_regex_matchers'](definitions, _DEBUG_PATTERNS_KEY, label, flags=re.MULTILINE),
        _definitions['get_named_regex_matchers'](definitions, _PLACEHOLDER_PATTERNS_KEY, label, flags=re.MULTILINE),
        _definitions['get_regex_matchers'](definitions, _PLACEHOLDER_DISCLOSURE_PATTERNS_KEY, label),
    )


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、依頼の範囲のイベント・最終応答・編集を集める。
    events = _transcript_events['get_turn_events'](transcript_path)[-1]
    return (
        events,
        _transcript_events['get_last_assistant_message'](events),
        _transcript_events['get_started_edit_tool_changes'](events),
    )


def _format_finding(finding):
    display_path, name, line = finding
    return f'- {display_path}: {name} ("{_truncate_text(line, _MAX_SNIPPET_LENGTH)}")'


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temporary-code-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, debug_patterns, placeholder_patterns, disclosure_patterns = _load_definitions(
            parsed_arguments.definitions_path
        )
        hook_input = _hook_input['read_hook_input']()

        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0

        cwd = _hook_input['get_cwd'](hook_input)
        transcript_path = _hook_input['get_transcript_path'](hook_input)

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答と編集を書き込み済みにする。
        _transcript_events['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        events, last_assistant_msg, changes = _get_turn_summary(transcript_path)
        if last_assistant_msg is None or not changes:
            return 0
        # 種類ごとの (理由の書き出し, 理由の書式, 照合する正規表現)。
        # 応答で未実装の箇所に触れていれば、仮実装はユーザーに伝わっているとみなして照合しない。
        kinds = [(_DEBUG_PREFIX, _DEBUG_REASON, debug_patterns)]
        if not any(pattern.search(last_assistant_msg) for pattern in disclosure_patterns):
            kinds.append((_PLACEHOLDER_PREFIX, _PLACEHOLDER_REASON, placeholder_patterns))
        # 新たに持ち込んだ行の照合は、共通utilityで行う。
        find_introduced_lines_by_regex = runpy.run_path(os.path.join(
            _utilities_directory, 'find-introduced-lines-by-regex', 'find-introduced-lines-by-regex.py',
        ))['find_introduced_lines_by_regex']
        # 同じ依頼で差し戻すのは種類ごとに1回だけにし、意図して残す場合に停止できなくなるのを防ぐ。
        # 複数の種類が当てはまる場合は1つずつ差し戻し、理由の書き出しで差し戻し済みの種類を判定できるようにする。
        reason = None
        for prefix, reason_format, patterns in kinds:
            if _transcript_events['is_already_requested'](events, prefix):
                continue
            findings = find_introduced_lines_by_regex(changes, cwd, excluded_file_patterns, patterns)
            if findings:
                reason = reason_format.format(
                    findings='\n'.join(_format_finding(finding) for finding in findings[:_MAX_FINDINGS]),
                )
                break
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect temporary code: {ex}', file=sys.stderr)
        return 2

    if reason is not None:
        _hook_output['write_block_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
