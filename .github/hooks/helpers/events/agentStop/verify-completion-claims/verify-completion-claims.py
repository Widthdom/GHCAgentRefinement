import json
import os
import re
import runpy
import sys
import time
from pathlib import Path


SHELL_TOOL_NAMES = {'bash', 'powershell'}
# テスト実行とみなすコマンド。`test-all-hooks.py`・`npm test`・`pytest` などを含み、`latest` などの語中は除く。
TEST_COMMAND_PATTERN = re.compile(
    r'(?<![a-z0-9])(?:tests?|pytest|unittest|jest|vitest|mocha|rspec|phpunit|ctest|tox|pester)(?![a-z])',
    re.IGNORECASE,
)
# 直後に否定が続く場合は主張とみなさない（例：「通っていない」「成功しなかった」）。
JAPANESE_NEGATION = r'(?![^。\n]{0,6}?(?:ない|ません|なかった|ず))'
TEST_CLAIM_PATTERNS = (
    re.compile(r'テスト[^。\n]{0,20}?(?:通過|通っ|通り|成功|パス|合格)' + JAPANESE_NEGATION),
    re.compile(r'\b(?:all\s+)?tests?\s+(?:(?:are|were|now|all)\s+)*(?:pass(?:ed|es|ing)?|succeed(?:ed|s)?)\b', re.IGNORECASE),
)
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


def _is_agent_stop_event_recorded(transcript_path, hook_timestamp):
    # トランスクリプトに、今回実行中のagentStop hookのhook.startイベントが書き込まれているかを返す。
    #   過去のターンのagentStopと取り違えないよう、hook入力のtimestampが一致するものだけを対象とする。
    with open(transcript_path, encoding='utf-8-sig') as file:
        for line in file:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # 空行や非同期追記中の不完全な行は読み飛ばし、待機後の本処理で改めて検証する。
                continue
            if not isinstance(event, dict) or event.get('type') != 'hook.start':
                continue
            data = event.get('data')
            if not isinstance(data, dict) or data.get('hookType') != 'agentStop':
                continue
            hook_input = data.get('input')
            if isinstance(hook_input, dict) and hook_input.get('timestamp') == hook_timestamp:
                return True
    return False


def get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・シェル実行・本hookによる差し戻し済みかを集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    last_assistant_msg = None
    shell_commands = {}
    shell_call_ids = []
    exit_codes = {}
    already_requested = False
    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects.py')
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


def get_claim_kind(response):
    # コードブロック内の出力例やコマンドは主張とみなさない。
    text = FENCED_CODE_PATTERN.sub('', response)
    if any(pattern.search(text) for pattern in TEST_CLAIM_PATTERNS):
        return 'test'
    if any(pattern.search(text) for pattern in VERIFICATION_CLAIM_PATTERNS):
        return 'verification'
    return None


def get_block_reason(claim_kind, shell_runs):
    if claim_kind == 'test':
        test_runs = [run for run in shell_runs if TEST_COMMAND_PATTERN.search(run[0])]
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
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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
        # GitHub Copilot はトランスクリプトに非同期で追記するため、自身のhook.startが書き込まれるまで最大5秒待つ。
        # イベントは発生順に追記されるので、hook.startがあれば最終応答とシェル実行の結果も書き込み済みとみなせる。
        hook_timestamp = hook_input.get('timestamp')
        if isinstance(hook_timestamp, (int, float)) and not isinstance(hook_timestamp, bool):
            deadline = time.monotonic() + 5.0
            while not _is_agent_stop_event_recorded(transcript_path, hook_timestamp):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)

        last_assistant_msg, shell_runs, already_requested = get_turn_summary(transcript_path)
    except (OSError, ValueError) as ex:
        print(f'Failed to inspect completion claims: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、根拠を示せない場合に停止できなくなるのを防ぐ。
    if last_assistant_msg is None or already_requested:
        return 0

    reason = get_block_reason(get_claim_kind(last_assistant_msg), shell_runs)
    if reason is not None:
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
