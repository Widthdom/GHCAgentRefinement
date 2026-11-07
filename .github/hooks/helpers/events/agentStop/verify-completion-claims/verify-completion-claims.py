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
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))


_SHELL_TOOL_NAMES = {'bash', 'powershell'}
_VERIFICATION_CLAIM_PATTERNS = (
    re.compile(r'動作確認(?:済|しました|した|が取れ|できた)'),
    re.compile(r'(?:動く|動作する|機能する)ことを確認(?:済|しました|した|できた)'),
    re.compile(r'\b(?:verified|confirmed)\s+(?:that\s+)?(?:it|this|the\s+\w+)\s+works\b', re.IGNORECASE),
)
_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
_REASON_PREFIX = 'The final response claims'
_EVIDENCE_REQUEST = (
    'Run the relevant command now and report its actual result. If you cannot run it, '
    'correct the claim and state explicitly that it was not verified. '
    'Do not claim success without evidence.'
)
_MAX_COMMAND_LENGTH = 200


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・シェル実行・本hookによる差し戻し済みかを集める。
    events, already_requested = _turn_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
    shell_commands = {}
    shell_call_ids = []
    exit_codes = {}
    for event_type, data in events:
        if event_type == 'tool.execution_start':
            tool_name = data.get('toolName')
            arguments = data.get('arguments')
            call_id = data.get('toolCallId')
            if (isinstance(tool_name, str) and tool_name.removeprefix('functions.') in _SHELL_TOOL_NAMES
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
    return _turn_events['get_last_assistant_message'](events), shell_runs, already_requested


def _get_claim_kind(response, check_test_runs):
    if check_test_runs['claims_tests_passed'](response):
        return 'test'
    # コードブロック内の出力例やコマンドは主張とみなさない。
    text = _FENCED_CODE_PATTERN.sub('', response)
    if any(pattern.search(text) for pattern in _VERIFICATION_CLAIM_PATTERNS):
        return 'verification'
    return None


def _get_block_reason(claim_kind, shell_runs, check_test_runs):
    if claim_kind == 'test':
        test_runs = [run for run in shell_runs if check_test_runs['is_test_command'](run[0])]
        if not test_runs:
            return (
                f'{_REASON_PREFIX} that tests passed, but no test command was run '
                f"since the user's latest request. {_EVIDENCE_REQUEST}"
            )
        command, exit_code = test_runs[-1]
        if exit_code != 0:
            if len(command) > _MAX_COMMAND_LENGTH:
                command = command[:_MAX_COMMAND_LENGTH] + '...'
            result = 'did not complete' if exit_code is None else f'exited with code {exit_code}'
            return (
                f"{_REASON_PREFIX} that tests passed, but the last test command since the user's "
                f'latest request {result}: {command}\n{_EVIDENCE_REQUEST}'
            )
        return None
    if claim_kind == 'verification' and not shell_runs:
        return (
            f'{_REASON_PREFIX} that the behavior was verified, but no command was run '
            f"since the user's latest request. {_EVIDENCE_REQUEST}"
        )
    return None


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0
        transcript_path = _hook_input['get_transcript_path'](hook_input)

        # テストの実行かどうかの判定は、共通utilityで行う。
        check_test_runs = runpy.run_path(
            os.path.join(_utilities_directory, 'check-test-runs', 'check-test-runs.py')
        )
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答とシェル実行の結果を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, shell_runs, already_requested = _get_turn_summary(transcript_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect completion claims: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、根拠を示せない場合に停止できなくなるのを防ぐ。
    if last_assistant_msg is None or already_requested:
        return 0

    reason = _get_block_reason(_get_claim_kind(last_assistant_msg, check_test_runs), shell_runs, check_test_runs)
    if reason is not None:
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
