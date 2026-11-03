import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


SHELL_TOOL_NAMES = {'bash', 'powershell'}
VERIFICATION_CLAIM_PATTERNS = (
    re.compile(r'動作確認(?:済|しました|した|が取れ|できた)'),
    re.compile(r'(?:動く|動作する|機能する)ことを確認(?:済|しました|した|できた)'),
    re.compile(r'\b(?:verified|confirmed)\s+(?:that\s+)?(?:it|this|the\s+\w+)\s+works\b', re.IGNORECASE),
)
FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
REASON_PREFIX = 'The final response claims'
EVIDENCE_REQUEST = (
    'Run the relevant command now and report its actual result. If you cannot run it, '
    'correct the claim and state explicitly that it was not verified. '
    'Do not claim success without evidence.'
)
MAX_COMMAND_LENGTH = 200


def get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・シェル実行・本hookによる差し戻し済みかを集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    last_assistant_msg = None
    shell_commands = {}
    shell_call_ids = []
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
                shell_call_ids.clear()
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
                shell_call_ids.append(call_id)
        elif event_type == 'tool.execution_complete':
            call_id = data.get('toolCallId')
            shell_execution = data.get('shellExecution')
            exit_code = shell_execution.get('exitCode') if isinstance(shell_execution, dict) else None
            # 拒否などで実行されなかった場合は終了コードが記録されないため、成功とみなさない。
            if isinstance(call_id, str) and type(exit_code) is int:
                exit_codes[call_id] = exit_code

    shell_runs = [(shell_commands[call_id], exit_codes.get(call_id)) for call_id in shell_call_ids]
    return last_assistant_msg, shell_runs, already_requested


def get_claim_kind(response, check_test_runs):
    if check_test_runs['claims_tests_passed'](response):
        return 'test'
    # コードブロック内の出力例やコマンドは主張とみなさない。
    text = FENCED_CODE_PATTERN.sub('', response)
    if any(pattern.search(text) for pattern in VERIFICATION_CLAIM_PATTERNS):
        return 'verification'
    return None


def get_block_reason(claim_kind, shell_runs, check_test_runs):
    if claim_kind == 'test':
        test_runs = [run for run in shell_runs if check_test_runs['is_test_command'](run[0])]
        if not test_runs:
            return (
                f'{REASON_PREFIX} that tests passed, but no test command was run '
                f"since the user's latest request. {EVIDENCE_REQUEST}"
            )
        command, exit_code = test_runs[-1]
        if exit_code != 0:
            if len(command) > MAX_COMMAND_LENGTH:
                command = command[:MAX_COMMAND_LENGTH] + '...'
            result = 'did not complete' if exit_code is None else f'exited with code {exit_code}'
            return (
                f"{REASON_PREFIX} that tests passed, but the last test command since the user's "
                f'latest request {result}: {command}\n{EVIDENCE_REQUEST}'
            )
        return None
    if claim_kind == 'verification' and not shell_runs:
        return (
            f'{REASON_PREFIX} that the behavior was verified, but no command was run '
            f"since the user's latest request. {EVIDENCE_REQUEST}"
        )
    return None


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

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

    # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
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
        # テストの実行かどうかの判定は、共通utilityで行う。
        check_test_runs = runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'check-test-runs' / 'check-test-runs.py')
        )
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答とシェル実行の結果を書き込み済みにする。
        runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event' / 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, shell_runs, already_requested = get_turn_summary(transcript_path)
    except (OSError, ValueError) as ex:
        print(f'Failed to inspect completion claims: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、根拠を示せない場合に停止できなくなるのを防ぐ。
    if last_assistant_msg is None or already_requested:
        return 0

    reason = get_block_reason(get_claim_kind(last_assistant_msg, check_test_runs), shell_runs, check_test_runs)
    if reason is not None:
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
