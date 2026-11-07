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
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


# 既存ファイルを書き換える組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
# パッチで既存の内容を書き換えるファイル。追加・削除・移動先は内容を把握する必要がないため対象外とする。
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* Update File: (.+?)\s*$', re.MULTILINE)
_DENY_REASON = (
    'Edit denied: you have not read the following existing files in this session: {files}. '
    'Editing from memory or assumptions risks failed replacements and broken surrounding code. '
    'View the relevant part of each file first, then redo the edit based on its current content.'
)


def _get_edited_paths(tool_name, tool_args):
    if tool_name == 'apply_patch':
        return [
            match for text in _collect_json_string_values(tool_args) for match in _PATCH_FILE_PATTERN.findall(text)
        ]
    if not isinstance(tool_args, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    return [path] if isinstance(path, str) and path.strip() else []


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
            _utilities_directory, 'known-files-state', 'known-files-state.py',
        ))

        hook_input = _hook_input['read_hook_input']()

        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        paths = set()
        tool_name = tool_name.removeprefix('functions.')
        if tool_name in _EDIT_TOOL_NAMES:
            tool_args = _unwrap_tool_args(tool_args)
            paths.update(
                state['normalize_path'](cwd, path) for path in _get_edited_paths(tool_name, tool_args)
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
