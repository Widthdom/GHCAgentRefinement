import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


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
        return [match for text in _collect_strings(arguments) for match in _PATCH_FILE_PATTERN.findall(text)]
    if not isinstance(arguments, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and arguments.get('command') == 'view':
        return []
    path = arguments.get('path')
    return [path] if isinstance(path, str) and path.strip() else []


def _collect_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _collect_strings(item)


def _get_turn_events(transcript_path):
    # ユーザーの最新の依頼以降の、編集ツールで変更したファイルとシェル実行を完了した順に集め、最終応答も返す。
    # 戻り値は (('edit', パス) または ('shell', (コマンド, 終了コード)) のリスト, 最終応答, 本hookによる差し戻し済みか)。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    events = []
    started = {}
    last_assistant_msg = None
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
                events.clear()
                started.clear()
                last_assistant_msg = None
                already_requested = False
            elif isinstance(content, str) and content.startswith(_REASON_PREFIX):
                already_requested = True
        elif event_type == 'assistant.message':
            content = data.get('content')
            if isinstance(content, str) and content.strip():
                last_assistant_msg = content
        elif event_type == 'tool.execution_start':
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
    return events, last_assistant_msg, already_requested


def _get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示す。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    absolute = os.path.normpath(os.path.join(cwd, path))
    try:
        if os.path.normcase(os.path.commonpath([cwd, absolute])) == os.path.normcase(cwd):
            return os.path.relpath(absolute, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return absolute.replace(os.sep, '/')


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
        display_path = _get_display_path(value, cwd)
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

    cwd = hook_input.get('cwd')
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        print('agentStop hook cwd must be an absolute path.', file=sys.stderr)
        return 2
    cwd = os.path.normpath(cwd)

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
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答と最後のツール実行の結果を書き込み済みにする。
        runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event' / 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        events, last_assistant_msg, already_requested = _get_turn_events(transcript_path)
    except (OSError, ValueError) as ex:
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
