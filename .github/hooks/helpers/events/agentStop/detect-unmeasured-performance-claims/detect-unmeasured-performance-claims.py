import argparse
import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


SHELL_TOOL_NAMES = {'bash', 'powershell'}
DEFINITION_KEYS = ('claimPatterns', 'hedgePatterns', 'measurementCommandPatterns')
FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
QUOTE_LINE_PATTERN = re.compile(r'^\s*>.*$', re.MULTILINE)
# 文の区切り。日本語の句点・感嘆符・疑問符、英語の文末の記号と空白、改行で区切る。
SENTENCE_SEPARATOR_PATTERN = re.compile(r'(?<=[。！？!?])|(?<=\.)\s+|\n')
MAX_CLAIM_LENGTH = 120
REASON_PREFIX = 'The final response claims a performance improvement'
BLOCK_REASON = (
    REASON_PREFIX + ' ("{claim}"), but no benchmark, profiler, or timing command was run successfully since '
    "the user's latest request. Performance claims without measurement are guesses: the change may be slower, "
    'or the difference may be noise. Measure it now (for example hyperfine, BenchmarkDotNet, pytest-benchmark, '
    'go test -bench, cargo bench, or a timed run before and after the change) and report the actual numbers. '
    'If you cannot measure it, rephrase the claim as an unverified expectation and say that it was not measured.'
)


def load_definitions(path):
    # 戻り値は {キー: [コンパイル済みの正規表現]}。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('performance claim definitions must be a JSON object.')
    loaded = {}
    for key in DEFINITION_KEYS:
        patterns = definitions.get(key)
        if not isinstance(patterns, list) or not patterns or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in patterns
        ):
            raise ValueError(f'performance claim definitions require a non-empty array of non-empty strings for {key}.')
        try:
            loaded[key] = [re.compile(pattern) for pattern in patterns]
        except re.PatternError as ex:
            raise ValueError(f'invalid regular expression in {key}: {ex}') from None
    return loaded


def get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・シェル実行・本hookによる差し戻し済みかを集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    last_assistant_msg = None
    shell_commands = {}
    exit_codes = {}
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
                shell_commands.clear()
                exit_codes.clear()
                already_requested = False
            elif isinstance(content, str) and content.startswith(REASON_PREFIX):
                already_requested = True
        elif event_type == 'assistant.message':
            content = data.get('content')
            if isinstance(content, str) and content.strip():
                last_assistant_msg = content
        elif event_type == 'tool.execution_start':
            tool_name = data.get('toolName')
            arguments = data.get('arguments')
            call_id = data.get('toolCallId')
            if (isinstance(tool_name, str) and tool_name.removeprefix('functions.') in SHELL_TOOL_NAMES
                    and isinstance(call_id, str) and isinstance(arguments, dict)
                    and isinstance(arguments.get('command'), str)):
                shell_commands[call_id] = arguments['command']
        elif event_type == 'tool.execution_complete':
            call_id = data.get('toolCallId')
            shell_execution = data.get('shellExecution')
            exit_code = shell_execution.get('exitCode') if isinstance(shell_execution, dict) else None
            # 拒否などで実行されなかった場合は終了コードが記録されないため、計測とみなさない。
            if isinstance(call_id, str) and type(exit_code) is int:
                exit_codes[call_id] = exit_code

    succeeded_commands = [command for call_id, command in shell_commands.items() if exit_codes.get(call_id) == 0]
    return last_assistant_msg, succeeded_commands, already_requested


def find_claim(definitions, response):
    # 戻り値は、断定している性能改善の主張を含む最初の文。なければNone。
    # コードブロックと引用は主張とみなさない。推測・否定を含む文（「速くなるはず」「計測していない」）は除く。
    text = QUOTE_LINE_PATTERN.sub('', FENCED_CODE_PATTERN.sub('', response))
    for sentence in SENTENCE_SEPARATOR_PATTERN.split(text):
        sentence = sentence.strip()
        if not sentence or not any(pattern.search(sentence) for pattern in definitions['claimPatterns']):
            continue
        if any(pattern.search(sentence) for pattern in definitions['hedgePatterns']):
            continue
        return sentence
    return None


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'performance-claim-definitions.json'),
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

        transcript_path = hook_input.get('transcriptPath')
        if not isinstance(transcript_path, str) or not transcript_path.strip():
            raise ValueError('transcriptPath must be a non-empty string.')
        if not os.path.isfile(transcript_path):
            raise ValueError(f'transcript path is not a file or could not be verified as a file: {transcript_path}')

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答とシェル実行の結果を書き込み済みにする。
        runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event' / 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, succeeded_commands, already_requested = get_turn_summary(transcript_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect unmeasured performance claims: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、計測できない場合でも停止できるようにする。
    if last_assistant_msg is None or already_requested:
        return 0
    claim = find_claim(definitions, last_assistant_msg)
    if claim is None:
        return 0
    if any(pattern.search(command) for command in succeeded_commands for pattern in definitions['measurementCommandPatterns']):
        return 0
    if len(claim) > MAX_CLAIM_LENGTH:
        claim = claim[:MAX_CLAIM_LENGTH] + '...'
    print(json.dumps(
        {'decision': 'block', 'reason': BLOCK_REASON.format(claim=claim)}, ensure_ascii=False, separators=(',', ':'),
    ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
