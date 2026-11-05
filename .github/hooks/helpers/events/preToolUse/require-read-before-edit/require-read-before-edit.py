import io
import json
import os
import re
import runpy
import sqlite3
import sys
from typing import cast


# 既存ファイルを書き換える組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
# パッチで既存の内容を書き換えるファイル。追加・削除・移動先は内容を把握する必要がないため対象外とする。
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* Update File: (.+?)\s*$', re.MULTILINE)
_DENY_REASON = (
    'Edit denied: you have not read the following existing files in this session: {files}. '
    'Editing from memory or assumptions risks failed replacements and broken surrounding code. '
    'View the relevant part of each file first, then redo the edit based on its current content.'
)


def _get_edited_paths(tool_name, tool_args, state):
    if tool_name == 'apply_patch':
        return [
            match for text in state['collect_strings'](tool_args) for match in _PATCH_FILE_PATTERN.findall(text)
        ]
    if not isinstance(tool_args, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    return [path] if isinstance(path, str) and path.strip() else []


def _get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path


def _deny_unread_files(state, state_path, paths):
    # 戻り値は、今回拒否するファイルパスのリスト。
    # 同じファイルの拒否は1回だけにし、読めない事情があっても編集を続けられるようにする。
    denied_paths = []
    with state['open_state'](state_path) as connection:
        for path in paths:
            key = state['get_file_key'](path)
            if connection.execute('SELECT 1 FROM files WHERE file = ?', (key,)).fetchone():
                continue
            connection.execute('INSERT INTO files (file, state) VALUES (?, ?)', (key, state['DENIED']))
            denied_paths.append(path)
    return denied_paths


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        # 記録側のpostToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'known-files-state', 'known-files-state.py',
        ))

        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')

        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd must be a non-empty string.')
        if not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        # preToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        paths = set()
        tool_name = tool_name.removeprefix('functions.')
        if tool_name in _EDIT_TOOL_NAMES:
            tool_args = state['get_tool_args'](hook_input['toolArgs'])
            paths.update(
                state['normalize_path'](cwd, path) for path in _get_edited_paths(tool_name, tool_args, state)
            )
        # 新規作成と、存在しないファイルへの編集（ツール側で失敗する）は対象外とする。
        existing_paths = sorted(path for path in paths if os.path.isfile(path))
        if not existing_paths:
            return 0
        denied_paths = _deny_unread_files(state, state_path, existing_paths)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check read-before-edit: {ex}', file=sys.stderr)
        return 2

    if denied_paths:
        normalized_cwd = os.path.normcase(os.path.realpath(cwd))
        files = ', '.join(_get_display_path(path, normalized_cwd) for path in denied_paths)
        # SDK形式のトップレベルpermissionDecision。正常な拒否なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({
            'permissionDecision': 'deny',
            'permissionDecisionReason': _DENY_REASON.format(files=files),
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
