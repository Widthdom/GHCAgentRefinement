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
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))


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


def _is_non_empty_string_list(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, str) and bool(item.strip()) for item in value))


def _load_definitions(definitions_path):
    # 定義ファイルはUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    if not os.path.isfile(definitions_path):
        raise ValueError(f'Definitions path is not a file or could not be verified as a file: {definitions_path}')
    try:
        with open(definitions_path, encoding='utf-8-sig') as file:
            definitions = json.load(file)
    except json.JSONDecodeError as ex:
        raise ValueError(f'Definitions are not valid JSON: {ex}') from ex
    if not isinstance(definitions, dict):
        raise ValueError('Definitions must be a JSON object.')

    for key in (_EVIDENCE_TOOL_NAMES_KEY, *_PATTERN_KEYS):
        if not _is_non_empty_string_list(definitions.get(key)):
            raise ValueError(f'Definitions require a non-empty array of non-empty strings for {key}.')
    patterns = {key: tuple(_build_regex_matchers(definitions[key], key)) for key in _PATTERN_KEYS}
    return set(definitions[_EVIDENCE_TOOL_NAMES_KEY]), patterns['pushbackPatterns'], patterns['reversalPatterns']


def _get_turn_summary(transcript_path, evidence_tool_names):
    # ユーザーの最新の依頼、その前の応答の有無、以降の最終応答・根拠確認ツールの使用・本hookによる差し戻し済みかを集める。
    turns = _turn_events['get_turns'](transcript_path)
    events = turns[-1]
    used_evidence_tool = any(
        event_type == 'tool.execution_start' and isinstance(data.get('toolName'), str)
        and data['toolName'].removeprefix('functions.') in evidence_tool_names
        for event_type, data in events
    )
    return (
        _turn_events['get_request'](events),
        len(turns) > 1 and _turn_events['get_last_assistant_message'](turns[-2]) is not None,
        _turn_events['get_last_assistant_message'](events),
        used_evidence_tool,
        _turn_events['is_already_requested'](events, _REASON_PREFIX),
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
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        dest='defs_path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sycophantic-reversal-definitions.json'),
    )
    parsed_args = parser.parse_args()

    try:
        evidence_tool_names, pushback_patterns, reversal_patterns = _load_definitions(parsed_args.defs_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to load sycophantic reversal definitions: {ex}', file=sys.stderr)
        return 2

    try:
        hook_input = _hook_input['read_hook_input']()
        # サブエージェントの停止は対象外。ユーザーの反論に応答するのは親のため。
        if hook_input.get('agentId'):
            return 0
        transcript_path = _hook_input['get_transcript_path'](hook_input)

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答とツール実行を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        summary = _get_turn_summary(transcript_path, evidence_tool_names)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect sycophantic reversal: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、確認できない場合に停止できなくなるのを防ぐ。
    *_, already_requested = summary
    if already_requested:
        return 0

    if _is_unverified_reversal(summary, pushback_patterns, reversal_patterns):
        _hook_output['write_block_decision'](_REASON)
    return 0


if __name__ == '__main__':
    sys.exit(main())
