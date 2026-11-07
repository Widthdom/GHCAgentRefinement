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
# 不安定なテストである旨を最終応答で明記していれば、成功を偽っていないため差し戻さない。
_FLAKY_NOTE_PATTERN = re.compile(r'flaky|intermittent|nondeterministic|不安定|間欠|偶発|再現性', re.IGNORECASE)
_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
_REASON_PREFIX = 'A test command passed only after being re-run without any change'
_MAX_COMMAND_LENGTH = 200


def _get_turn_events(transcript_path, extract_edit_tool_changes):
    # ユーザーの最新の依頼以降の、ファイル編集とシェル実行を完了した順に集め、最終応答も返す。
    # 戻り値は ('edit', None) または ('shell', (コマンド, 終了コード)) のリスト, 最終応答, 本hookによる差し戻し済みか。
    turn_events, already_requested = _turn_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
    events = []
    started = {}
    for event_type, data in turn_events:
        if event_type == 'tool.execution_start':
            call_id = data.get('toolCallId')
            tool_name = data.get('toolName')
            if isinstance(call_id, str) and isinstance(tool_name, str):
                started[call_id] = (tool_name.removeprefix('functions.'), data.get('arguments'))
        elif event_type == 'tool.execution_complete':
            call_id = data.get('toolCallId')
            if call_id not in started:
                continue
            tool_name, arguments = started.pop(call_id)
            if tool_name in _SHELL_TOOL_NAMES:
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        pass
                if isinstance(arguments, dict) and isinstance(arguments.get('command'), str):
                    shell_execution = data.get('shellExecution')
                    exit_code = shell_execution.get('exitCode') if isinstance(shell_execution, dict) else None
                    events.append(('shell', (arguments['command'], exit_code if type(exit_code) is int else None)))
            elif data.get('success') is True and extract_edit_tool_changes(tool_name, arguments, include_deleted=True):
                events.append(('edit', None))
    return events, _turn_events['get_last_assistant_message'](turn_events), already_requested


def _normalize_command(command):
    return ' '.join(command.split())


def _find_rerun_until_green(events, is_test_command):
    # 最後のテスト実行が成功していて、その直前に同じコマンドが失敗したまま、間にファイルの編集も
    # 別のコマンドの実行もなければ、(コマンド, 成功までの失敗回数) を返す。
    # 別のコマンド（依存の導入やキャッシュの削除など）は失敗の原因を取り除いた可能性があるため、区切りとする。
    last_test_index = next(
        (index for index in range(len(events) - 1, -1, -1)
         if events[index][0] == 'shell' and is_test_command(events[index][1][0])),
        None,
    )
    if last_test_index is None:
        return None
    command, exit_code = events[last_test_index][1]
    if exit_code != 0:
        return None

    normalized = _normalize_command(command)
    failures = 0
    for kind, value in reversed(events[:last_test_index]):
        if kind != 'shell' or _normalize_command(value[0]) != normalized:
            break
        if value[1] == 0:
            # 成功が続いているだけなら、失敗を数えず遡る。
            continue
        if value[1] is None:
            break
        failures += 1
    if failures == 0:
        return None
    return command, failures


def _get_block_reason(command, failures):
    if len(command) > _MAX_COMMAND_LENGTH:
        command = command[:_MAX_COMMAND_LENGTH] + '...'
    times = 'once' if failures == 1 else f'{failures} times'
    return (
        f'{_REASON_PREFIX} ({command}): it failed {times} and then passed with no file edit or other command in between, '
        'yet the final response claims that tests passed. A pass obtained by re-running is not evidence that the code works; '
        'the test is likely flaky or depends on timing, ordering, or external state. '
        'Investigate why the earlier run failed and fix the cause, or state explicitly in the final response '
        'that the test is flaky and passed only on a re-run.'
    )


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

        # テストの実行かどうかの判定と、編集ツールの引数の解析は、共通utilityで行う。
        check_test_runs = runpy.run_path(os.path.join(_utilities_directory, 'check-test-runs', 'check-test-runs.py'))
        extract_edit_tool_changes = runpy.run_path(
            os.path.join(_utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py')
        )['extract_edit_tool_changes']
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答と最後のツール実行の結果を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        events, last_assistant_msg, already_requested = _get_turn_events(transcript_path, extract_edit_tool_changes)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect test re-runs: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、原因を突き止められない場合に停止できなくなるのを防ぐ。
    if (last_assistant_msg is None or already_requested
            or not check_test_runs['claims_tests_passed'](last_assistant_msg)
            or _FLAKY_NOTE_PATTERN.search(_FENCED_CODE_PATTERN.sub('', last_assistant_msg))):
        return 0

    rerun = _find_rerun_until_green(events, check_test_runs['is_test_command'])
    if rerun is not None:
        print(json.dumps(
            {'decision': 'block', 'reason': _get_block_reason(*rerun)},
            ensure_ascii=False, separators=(',', ':'),
        ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
