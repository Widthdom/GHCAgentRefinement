import io
import os
import re
import sqlite3
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'parse_tool_calls'),
]
import read_hook_input as _hook_input
import parse_tool_calls as _tool_calls


# コミットのメッセージと差分を表示するgitのコマンド。`git -C <dir> show`・`git --no-pager log` なども含む。
_VIEW_COMMAND_PATTERN = re.compile(r'\bgit\b[^\n;&|]*?\s(?:show|log)\b')
# `git show`・`git log` の既定の書式の見出し行。SHA-1とSHA-256のリポジトリの両方を扱う。
_COMMIT_HEADER_PATTERN = re.compile(r'^commit ([0-9a-f]{64}|[0-9a-f]{40})\b', re.MULTILINE)
_ANSI_ESCAPE_PATTERN = re.compile(r'\x1b\[[0-9;]*m')


def main():
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        # 拒否側のpreToolUse hookと状態の形式・保存先を共有する。
        sys.path.insert(0, os.path.join(_utilities_directory, 'viewed_commits_state'))
        import viewed_commits_state as state

        hook_input = _hook_input.read_hook_input()
        state_path = state.get_state_path(hook_input.get('sessionId'))
        # postToolUseには、呼び出したツール1件の名前（toolName）と引数（toolArgs）、結果（toolResult）が渡される。
        tool_name, tool_args = _hook_input.get_tool_call(hook_input)

        if not _tool_calls.is_shell_tool(tool_name):
            return 0
        tool_args = _tool_calls.unwrap_tool_args(tool_args)
        command = tool_args.get('command') if isinstance(tool_args, dict) else None
        if not isinstance(command, str) or not _VIEW_COMMAND_PATTERN.search(command):
            return 0
        tool_result = hook_input.get('toolResult')
        output = tool_result.get('textResultForLlm') if isinstance(tool_result, dict) else None
        if not isinstance(output, str):
            return 0

        # 見出し行のあるコミットだけを、メッセージを読んだものとして記録する。`--oneline` などの一覧は対象外。
        commit_ids = set(_COMMIT_HEADER_PATTERN.findall(_ANSI_ESCAPE_PATTERN.sub('', output)))
        if commit_ids:
            state.record_viewed(state_path, sorted(commit_ids))
    except (OSError, UnicodeError, ValueError, ImportError, sqlite3.Error) as ex:
        print(f'Failed to record viewed commits: {ex}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
