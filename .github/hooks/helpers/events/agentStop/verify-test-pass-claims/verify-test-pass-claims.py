import io
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
_truncate_text = runpy.run_path(os.path.join(_utilities_directory, 'truncate-text', 'truncate-text.py'))['truncate_text']
_tool_kinds = runpy.run_path(os.path.join(_utilities_directory, 'check-tool-kinds', 'check-tool-kinds.py'))
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


_VERIFICATION_CLAIM_PATTERNS = (
    re.compile(r'動作確認(?:済|しました|した|が取れ|できた)'),
    re.compile(r'(?:動く|動作する|機能する)ことを確認(?:済|しました|した|できた)'),
    re.compile(r'\b(?:verified|confirmed)\s+(?:that\s+)?(?:it|this|the\s+\w+)\s+works\b', re.IGNORECASE),
)
# 不安定なテストである旨を最終応答で明記していれば、再実行で通ったことを偽っていないため差し戻さない。
_FLAKY_NOTE_PATTERN = re.compile(r'flaky|intermittent|nondeterministic|不安定|間欠|偶発|再現性', re.IGNORECASE)
_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
# 説明だけのファイル。変更してもテストの結果は変わらないため、未テストの変更に数えない。
_DOCUMENT_EXTENSIONS = ('.md', '.markdown', '.rst', '.txt', '.adoc')
# hookのファイル。変更後のテストはrequire-hook-tests hookが主張の有無によらず求めるため、重ねて差し戻さない。
_HOOKS_PATH_PATTERN = re.compile(r'(?:^|/)\.github/hooks/', re.IGNORECASE)
# 差し戻しの理由の書き出しは種類ごとに変え、同じ依頼での差し戻しを種類ごとに1回に限る。
_UNVERIFIED_PREFIX = 'The final response claims that'
_RERUN_PREFIX = 'A test command passed only after being re-run without any change'
_STALE_PREFIX = 'Files were edited after the last passing test run'
_EVIDENCE_REQUEST = (
    'Run the relevant command now and report its actual result. If you cannot run it, '
    'correct the claim and state explicitly that it was not verified. '
    'Do not claim success without evidence.'
)
_MAX_LISTED_FILES = 10
_MAX_COMMAND_LENGTH = 200


def _get_turn_summary(transcript_path, extract_edit_tool_changes):
    # ユーザーの最新の依頼以降の、ファイル編集とシェル実行を完了した順に集め、依頼の範囲のイベントと最終応答も返す。
    # 編集は ('edit', 変更したファイルのパスのリスト)、シェル実行は ('shell', (コマンド, 終了コード)) とする。
    turn_events = _turn_events['get_turns'](transcript_path)[-1]
    events = []
    # 完了していないシェル実行は、終了コードのない実行として最後に加わる。
    for tool_name, arguments, completion in _turn_events['get_tool_calls'](turn_events):
        command = _get_shell_command(tool_name, arguments)
        if command is not None:
            # 拒否などで実行されなかった場合は終了コードが記録されないため、成功とみなさない。
            events.append(('shell', (command, _turn_events['get_shell_exit_code'](completion))))
        elif completion is not None and completion.get('success') is True:
            changes = extract_edit_tool_changes(tool_name, arguments, include_deleted=True)
            if changes:
                events.append(('edit', [
                    path for change in changes for path in (change['path'], change['moved_to']) if path is not None
                ]))
    return events, turn_events, _turn_events['get_last_assistant_message'](turn_events)


def _get_shell_command(tool_name, arguments):
    if not _tool_kinds['is_shell_tool'](tool_name):
        return None
    arguments = _unwrap_tool_args(arguments)
    command = arguments.get('command') if isinstance(arguments, dict) else None
    return command if isinstance(command, str) else None


def _shorten_command(command):
    return _truncate_text(command, _MAX_COMMAND_LENGTH)


def _normalize_command(command):
    return ' '.join(command.split())


def _get_unverified_reason(events, last_test_index, check_test_runs):
    # テストを1回も実行していないか、最後のテストが成功していなければ、理由を返す。
    # 後に `;`・`||` で別のコマンドが続くなど、終了コードがテストの結果を表さない場合も成功とみなさない。
    if last_test_index is None:
        return (
            f'{_UNVERIFIED_PREFIX} tests passed, but no test command was run '
            f"since the user's latest request. {_EVIDENCE_REQUEST}"
        )
    command, exit_code = events[last_test_index][1]
    if exit_code == 0 and check_test_runs['is_test_result_reported'](command):
        return None
    if exit_code == 0:
        result = (
            'exited with code 0, but its exit status does not reflect the test result '
            '(another command follows with ";", "||" or "&", or the test is piped without pipefail)'
        )
    else:
        result = 'did not complete' if exit_code is None else f'exited with code {exit_code}'
    return (
        f"{_UNVERIFIED_PREFIX} tests passed, but the last test command since the user's "
        f'latest request {result}: {_shorten_command(command)}\n{_EVIDENCE_REQUEST}'
    )


def _get_rerun_reason(events, last_test_index):
    # 最後のテストの直前に同じコマンドが失敗したまま、間にファイルの編集も別のコマンドの実行もなければ、理由を返す。
    # 別のコマンド（依存の導入やキャッシュの削除など）は失敗の原因を取り除いた可能性があるため、区切りとする。
    command = events[last_test_index][1][0]
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
    times = 'once' if failures == 1 else f'{failures} times'
    return (
        f'{_RERUN_PREFIX} ({_shorten_command(command)}): it failed {times} and then passed with no file edit or other '
        'command in between, yet the final response claims that tests passed. A pass obtained by re-running is not '
        'evidence that the code works; the test is likely flaky or depends on timing, ordering, or external state. '
        'Investigate why the earlier run failed and fix the cause, or state explicitly in the final response '
        'that the test is flaky and passed only on a re-run.'
    )


def _get_stale_reason(events, last_test_index, cwd):
    # 最後のテストの後に、説明だけのファイルとhookのファイル以外を変更していれば、理由を返す。
    stale_paths = []
    seen = set()
    for kind, value in events[last_test_index + 1:]:
        if kind != 'edit':
            continue
        for path in value:
            display_path = _get_display_path(os.path.normpath(os.path.join(cwd, path)), cwd)
            if display_path.lower().endswith(_DOCUMENT_EXTENSIONS) or _HOOKS_PATH_PATTERN.search(display_path):
                continue
            key = os.path.normcase(display_path)
            if key not in seen:
                seen.add(key)
                stale_paths.append(display_path)
    if not stale_paths:
        return None
    lines = [
        f'{_STALE_PREFIX} ({_shorten_command(events[last_test_index][1][0])}), but the final response claims that '
        'tests passed. The passing result does not cover these later edits:'
    ]
    lines.extend(f'- {path}' for path in stale_paths[:_MAX_LISTED_FILES])
    if len(stale_paths) > _MAX_LISTED_FILES:
        lines.append(f'- ... and {len(stale_paths) - _MAX_LISTED_FILES} more')
    lines.append(
        'Re-run the relevant tests now and report the actual result. If you cannot run them, '
        'state explicitly that the latest edits were not tested.'
    )
    return '\n'.join(lines)


def _get_block_reasons(response, events, cwd, check_test_runs):
    # 当てはまる差し戻しを、(理由の書き出し, 理由) の組で検査の順に返す。
    if not check_test_runs['claims_tests_passed'](response):
        # コードブロック内の出力例やコマンドは主張とみなさない。
        text = _FENCED_CODE_PATTERN.sub('', response)
        if any(pattern.search(text) for pattern in _VERIFICATION_CLAIM_PATTERNS) and not any(
            kind == 'shell' for kind, _ in events
        ):
            return [(_UNVERIFIED_PREFIX, (
                f'{_UNVERIFIED_PREFIX} the behavior was verified, but no command was run '
                f"since the user's latest request. {_EVIDENCE_REQUEST}"
            ))]
        return []

    last_test_index = next(
        (index for index in range(len(events) - 1, -1, -1)
         if events[index][0] == 'shell' and check_test_runs['is_test_command'](events[index][1][0])),
        None,
    )
    unverified = _get_unverified_reason(events, last_test_index, check_test_runs)
    if unverified is not None:
        return [(_UNVERIFIED_PREFIX, unverified)]
    # 再実行だけで通ったテストを先に示す。未テストの変更を先に示してテストを再実行させると、
    # 先の失敗と最後のテストの間に編集が挟まり、再実行の検査に当てはまらなくなるため。
    reasons = []
    if not _FLAKY_NOTE_PATTERN.search(_FENCED_CODE_PATTERN.sub('', response)):
        reasons.append((_RERUN_PREFIX, _get_rerun_reason(events, last_test_index)))
    reasons.append((_STALE_PREFIX, _get_stale_reason(events, last_test_index, cwd)))
    return [(prefix, reason) for prefix, reason in reasons if reason is not None]


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

        # テストの実行かどうかの判定と、編集ツールの引数の解析は、共通utilityで行う。
        check_test_runs = runpy.run_path(os.path.join(_utilities_directory, 'check-test-runs', 'check-test-runs.py'))
        extract_edit_tool_changes = runpy.run_path(
            os.path.join(_utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py')
        )['extract_edit_tool_changes']
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答と最後のツール実行の結果を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        events, turn_events, last_assistant_msg = _get_turn_summary(transcript_path, extract_edit_tool_changes)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect test pass claims: {ex}', file=sys.stderr)
        return 2

    if last_assistant_msg is None:
        return 0
    # 同じ依頼で差し戻すのは種類ごとに1回だけにし、根拠を示せない場合に停止できなくなるのを防ぐ。
    # 複数の種類が当てはまる場合は1つずつ差し戻し、理由の書き出しで差し戻し済みの種類を判定できるようにする。
    for prefix, reason in _get_block_reasons(last_assistant_msg, events, cwd, check_test_runs):
        if not _turn_events['is_already_requested'](turn_events, prefix):
            _hook_output['write_block_decision'](reason)
            break
    return 0


if __name__ == '__main__':
    sys.exit(main())
