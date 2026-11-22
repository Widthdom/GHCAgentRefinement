import argparse
import io
import os
import re
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'parse_tool_calls'),
    os.path.join(_utilities_directory, 'write_hook_output'),
    os.path.join(_utilities_directory, 'read_definitions'),
    os.path.join(_utilities_directory, 'read_transcript_events'),
]
import read_hook_input as _hook_input
import parse_tool_calls as _tool_calls
import write_hook_output as _hook_output
import read_definitions as _definitions
import read_transcript_events as _transcript_events


# 定義ファイルのキー。evidenceToolNamesは根拠の確認とみなすツール名、pushbackPatternsはユーザーの反論・疑問、
# reversalPatternsは応答が主張を翻したとみなす正規表現。
_EVIDENCE_TOOL_NAMES_KEY = 'evidenceToolNames'
_PATTERN_KEYS = ('pushbackPatterns', 'reversalPatterns')
_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
_REASON_PREFIX = 'The final response reverses'
_REASON = (
    f"{_REASON_PREFIX} the earlier position right after the user's pushback, but no evidence-gathering tool "
    '(such as view, rg, or bash) was used since that message. Check the relevant code, documentation, '
    'or command output now. If the evidence supports the user, explain what it shows. If it supports '
    'the earlier answer, keep that answer and explain why. Do not change your position only because '
    'the user disagreed.'
)


def _strip_code(text):
    return _FENCED_CODE_PATTERN.sub('', text)


def _load_definitions(definitions_path):
    label = 'sycophantic reversal definitions'
    definitions = _definitions.read_definitions(definitions_path, label)
    evidence_tool_names = set(_definitions.get_string_list(definitions, _EVIDENCE_TOOL_NAMES_KEY, label))
    pushback_patterns, reversal_patterns = (tuple(_definitions.get_regex_matchers(definitions, key, label)) for key in _PATTERN_KEYS)
    return evidence_tool_names, pushback_patterns, reversal_patterns


def _get_turn_summary(transcript_path, evidence_tool_names):
    # ユーザーの最新の依頼、その前の応答の有無、以降の最終応答・根拠確認ツールの使用・本hookによる差し戻し済みかを集める。
    turns = _transcript_events.get_turn_events(transcript_path)
    events = turns[-1]
    used_evidence_tool = any(
        event_type == 'tool.execution_start' and isinstance(data.get('toolName'), str)
        and _tool_calls.get_tool_name(data['toolName']) in evidence_tool_names
        for event_type, data in events
    )
    return (
        _transcript_events.get_request(events),
        len(turns) > 1 and _transcript_events.get_last_assistant_message(turns[-2]) is not None,
        _transcript_events.get_last_assistant_message(events),
        used_evidence_tool,
        _transcript_events.is_already_requested(events, _REASON_PREFIX),
    )


def _is_unverified_reversal(summary, pushback_patterns, reversal_patterns):
    latest_request, had_prior_response, last_assistant_msg, used_evidence_tool, _ = summary
    # 翻す対象となる前の応答がない場合と、根拠を確認した上で翻した場合は対象外。
    if not had_prior_response or used_evidence_tool or latest_request is None or last_assistant_msg is None:
        return False
    request = _strip_code(latest_request)
    response = _strip_code(last_assistant_msg)
    return (any(pattern.search(request) for pattern in pushback_patterns)
            and any(pattern.search(response) for pattern in reversal_patterns))


def main():
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        dest='defs_path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sycophantic_reversal_definitions.json'),
    )
    parsed_args = parser.parse_args()

    try:
        evidence_tool_names, pushback_patterns, reversal_patterns = _load_definitions(parsed_args.defs_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to load sycophantic reversal definitions: {ex}', file=sys.stderr)
        return 2

    try:
        hook_input = _hook_input.read_hook_input()
        # サブエージェントの停止は対象外。ユーザーの反論に応答するのは親のため。
        if hook_input.get('agentId'):
            return 0
        transcript_path = _hook_input.get_transcript_path(hook_input)

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答とツール実行を書き込み済みにする。
        _transcript_events.wait_for_agent_stop_event(transcript_path, hook_input.get('timestamp'))

        summary = _get_turn_summary(transcript_path, evidence_tool_names)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect sycophantic reversal: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、確認できない場合に停止できなくなるのを防ぐ。
    *_, already_requested = summary
    if already_requested:
        return 0

    if _is_unverified_reversal(summary, pushback_patterns, reversal_patterns):
        _hook_output.write_block_decision(_REASON)
    return 0


if __name__ == '__main__':
    sys.exit(main())
