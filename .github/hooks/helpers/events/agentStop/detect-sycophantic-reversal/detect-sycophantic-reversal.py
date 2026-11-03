import argparse
import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


# 定義ファイルのキー。evidenceToolNamesは根拠の確認とみなすツール名、pushbackPatternsはユーザーの反論・疑問、
# reversalPatternsは応答が主張を翻したとみなす正規表現。
EVIDENCE_TOOL_NAMES_KEY = 'evidenceToolNames'
PATTERN_KEYS = ('pushbackPatterns', 'reversalPatterns')
FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
REASON_PREFIX = 'The final response reverses'
REASON = (
    f"{REASON_PREFIX} the earlier position right after the user's pushback, but no evidence-gathering tool "
    '(such as view, rg, or bash) was used since that message. Check the relevant code, documentation, '
    'or command output now. If the evidence supports the user, explain what it shows. If it supports '
    'the earlier answer, keep that answer and explain why. Do not change your position only because '
    'the user disagreed.'
)


def strip_code(text):
    return FENCED_CODE_PATTERN.sub('', text)


def is_non_empty_string_list(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, str) and bool(item.strip()) for item in value))


def load_definitions(definitions_path):
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

    for key in (EVIDENCE_TOOL_NAMES_KEY, *PATTERN_KEYS):
        if not is_non_empty_string_list(definitions.get(key)):
            raise ValueError(f'Definitions require a non-empty array of non-empty strings for {key}.')
    patterns = {}
    for key in PATTERN_KEYS:
        try:
            patterns[key] = tuple(re.compile(pattern) for pattern in definitions[key])
        except re.PatternError as ex:
            raise ValueError(f'Invalid regular expression in {key}: {ex}') from ex
    return set(definitions[EVIDENCE_TOOL_NAMES_KEY]), patterns['pushbackPatterns'], patterns['reversalPatterns']


def get_turn_summary(transcript_path, evidence_tool_names):
    # ユーザーの最新の依頼、その前の応答の有無、以降の最終応答・根拠確認ツールの使用・本hookによる差し戻し済みかを集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    latest_request = None
    had_prior_response = False
    last_assistant_msg = None
    used_evidence_tool = False
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
                had_prior_response = last_assistant_msg is not None
                latest_request = content if isinstance(content, str) else None
                last_assistant_msg = None
                used_evidence_tool = False
                already_requested = False
            elif isinstance(content, str) and content.startswith(REASON_PREFIX):
                already_requested = True
        elif event_type == 'assistant.message':
            content = data.get('content')
            if isinstance(content, str) and content.strip():
                last_assistant_msg = content
        elif event_type == 'tool.execution_start':
            tool_name = data.get('toolName')
            if isinstance(tool_name, str) and tool_name.removeprefix('functions.') in evidence_tool_names:
                used_evidence_tool = True

    return latest_request, had_prior_response, last_assistant_msg, used_evidence_tool, already_requested


def is_unverified_reversal(summary, pushback_patterns, reversal_patterns):
    latest_request, had_prior_response, last_assistant_msg, used_evidence_tool, _ = summary
    # 翻す対象となる前の応答がない場合と、根拠を確認した上で翻した場合は対象外。
    if not had_prior_response or used_evidence_tool or latest_request is None or last_assistant_msg is None:
        return False
    request = strip_code(latest_request)
    response = strip_code(last_assistant_msg)
    return (any(pattern.search(request) for pattern in pushback_patterns)
            and any(pattern.search(response) for pattern in reversal_patterns))


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        dest='defs_path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sycophantic-reversal-definitions.json'),
    )
    parsed_args = parser.parse_args()

    try:
        evidence_tool_names, pushback_patterns, reversal_patterns = load_definitions(parsed_args.defs_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to load sycophantic reversal definitions: {ex}', file=sys.stderr)
        return 2

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            print('agentStop hook input is empty.', file=sys.stderr)
            return 2
        hook_input = json.loads(hook_input_json)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f'Failed to parse agentStop hook input: {ex}', file=sys.stderr)
        return 2

    if not isinstance(hook_input, dict):
        print('agentStop hook input must be a JSON object.', file=sys.stderr)
        return 2

    # サブエージェントの停止は対象外。ユーザーの反論に応答するのは親のため。
    if hook_input.get('agentId'):
        return 0

    transcript_path = hook_input.get('transcriptPath')
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        print('agentStop hook transcriptPath must be a non-empty string.', file=sys.stderr)
        return 2

    if not os.path.isfile(transcript_path):
        print(f'Transcript path is not a file or could not be verified as a file: {transcript_path}', file=sys.stderr)
        return 2

    try:
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答とツール実行を書き込み済みにする。
        runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event' / 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        summary = get_turn_summary(transcript_path, evidence_tool_names)
    except (OSError, ValueError) as ex:
        print(f'Failed to inspect sycophantic reversal: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、確認できない場合に停止できなくなるのを防ぐ。
    *_, already_requested = summary
    if already_requested:
        return 0

    if is_unverified_reversal(summary, pushback_patterns, reversal_patterns):
        print(json.dumps({'decision': 'block', 'reason': REASON}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
