import io
import json
import os
import re
import runpy
import sqlite3
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))


_SHELL_TOOL_NAMES = frozenset({'bash', 'powershell'})
# コミットのメッセージと差分を表示するgitのコマンド。`git -C <dir> show`・`git --no-pager log` なども含む。
_VIEW_COMMAND_PATTERN = re.compile(r'\bgit\b[^\n;&|]*?\s(?:show|log)\b')
# `git show`・`git log` の既定の書式の見出し行。SHA-1とSHA-256のリポジトリの両方を扱う。
_COMMIT_HEADER_PATTERN = re.compile(r'^commit ([0-9a-f]{64}|[0-9a-f]{40})\b', re.MULTILINE)
_ANSI_ESCAPE_PATTERN = re.compile(r'\x1b\[[0-9;]*m')


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        # 拒否側のpreToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'viewed-commits-state', 'viewed-commits-state.py',
        ))

        hook_input = _hook_input['read_hook_input']()
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        # postToolUseには、呼び出したツール1件の名前（toolName）と引数（toolArgs）、結果（toolResult）が渡される。
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        if tool_name.removeprefix('functions.') not in _SHELL_TOOL_NAMES:
            return 0
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except ValueError:
                return 0
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
            state['record_viewed'](state_path, sorted(commit_ids))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to record viewed commits: {ex}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
