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
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']


_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_SHELL_TOOL_NAMES = {'bash', 'powershell'}
# パッチで追加・更新・削除するファイルと移動先。
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$', re.MULTILINE)
# 説明だけのファイル。変更してもテストの結果は変わらないため対象外とする。
_DOCUMENT_EXTENSIONS = ('.md', '.markdown', '.rst', '.txt', '.adoc')
# hookのファイル。変更後のテストはrequire-hook-tests hookが主張の有無によらず求めるため、重ねて差し戻さない。
_HOOKS_PATH_PATTERN = re.compile(r'(?:^|/)\.github/hooks/', re.IGNORECASE)
_REASON_PREFIX = 'Files were edited after the last passing test run'
_MAX_LISTED_FILES = 10
_MAX_COMMAND_LENGTH = 200


def _get_edited_paths(tool_name, arguments):
    if tool_name == 'apply_patch':
        return [match for text in _collect_json_string_values(arguments) for match in _PATCH_FILE_PATTERN.findall(text)]
    if not isinstance(arguments, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and arguments.get('command') == 'view':
        return []
    path = arguments.get('path')
    return [path] if isinstance(path, str) and path.strip() else []


def _get_turn_events(transcript_path):
    # ユーザーの最新の依頼以降の、編集ツールで変更したファイルとシェル実行を完了した順に集め、最終応答も返す。
    # 戻り値は (('edit', パス) または ('shell', (コマンド, 終了コード)) のリスト, 最終応答, 本hookによる差し戻し済みか)。
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
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    pass
            if tool_name in _EDIT_TOOL_NAMES and data.get('success') is True:
                events.extend(('edit', path) for path in _get_edited_paths(tool_name, arguments))
            elif (tool_name in _SHELL_TOOL_NAMES and isinstance(arguments, dict)
                    and isinstance(arguments.get('command'), str)):
                shell_execution = data.get('shellExecution')
                exit_code = shell_execution.get('exitCode') if isinstance(shell_execution, dict) else None
                # 拒否などで実行されなかった場合は終了コードが記録されないため、成功とみなさない。
                events.append(('shell', (arguments['command'], exit_code if type(exit_code) is int else None)))
    return events, _turn_events['get_last_assistant_message'](turn_events), already_requested


def _get_stale_edits(events, cwd, is_test_command):
    # 最後のテスト実行が成功し、その後に対象のファイルを変更していれば、(テストコマンド, 変更したファイル) を返す。
    # 最後のテスト実行が失敗・未実行の場合は、verify-completion-claims hookが扱うため対象外とする。
    last_test_index = None
    for index, (kind, value) in enumerate(events):
        if kind == 'shell' and is_test_command(value[0]):
            last_test_index = index
    if last_test_index is None:
        return None
    command, exit_code = events[last_test_index][1]
    if exit_code != 0:
        return None

    stale_paths = []
    seen = set()
    for kind, value in events[last_test_index + 1:]:
        if kind != 'edit':
            continue
        display_path = _get_display_path(os.path.normpath(os.path.join(cwd, value)), cwd)
        if display_path.lower().endswith(_DOCUMENT_EXTENSIONS) or _HOOKS_PATH_PATTERN.search(display_path):
            continue
        key = os.path.normcase(display_path)
        if key not in seen:
            seen.add(key)
            stale_paths.append(display_path)
    if not stale_paths:
        return None
    return command, stale_paths


def _get_block_reason(command, stale_paths):
    if len(command) > _MAX_COMMAND_LENGTH:
        command = command[:_MAX_COMMAND_LENGTH] + '...'
    lines = [
        f'{_REASON_PREFIX} ({command}), but the final response claims that tests passed. '
        'The passing result does not cover these later edits:'
    ]
    lines.extend(f'- {path}' for path in stale_paths[:_MAX_LISTED_FILES])
    if len(stale_paths) > _MAX_LISTED_FILES:
        lines.append(f'- ... and {len(stale_paths) - _MAX_LISTED_FILES} more')
    lines.append(
        'Re-run the relevant tests now and report the actual result. If you cannot run them, '
        'state explicitly that the latest edits were not tested.'
    )
    return '\n'.join(lines)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0
        cwd = os.path.normpath(_hook_input['get_cwd'](hook_input))
        transcript_path = _hook_input['get_transcript_path'](hook_input)

        # テストの実行かどうかの判定は、共通utilityで行う。
        check_test_runs = runpy.run_path(
            os.path.join(_utilities_directory, 'check-test-runs', 'check-test-runs.py')
        )
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答と最後のツール実行の結果を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        events, last_assistant_msg, already_requested = _get_turn_events(transcript_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect stale verification: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、テストを実行できない場合に停止できなくなるのを防ぐ。
    if (last_assistant_msg is None or already_requested
            or not check_test_runs['claims_tests_passed'](last_assistant_msg)):
        return 0

    stale = _get_stale_edits(events, cwd, check_test_runs['is_test_command'])
    if stale is not None:
        print(json.dumps(
            {'decision': 'block', 'reason': _get_block_reason(*stale)},
            ensure_ascii=False, separators=(',', ':'),
        ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
