import io
import os
import runpy
import sqlite3
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read_hook_input.py'))
_tool_calls = runpy.run_path(os.path.join(_utilities_directory, 'parse-tool-calls', 'parse_tool_calls.py'))
_edit_tool_changes = runpy.run_path(os.path.join(_utilities_directory, 'edit-tool-changes', 'edit_tool_changes.py'))


# 成功すればファイルの内容を把握したとみなせる組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
#   view・str_replace_editorのview：内容を読んだ。
#   編集系：置換やパッチの適用には現在の内容との一致が必要で、作成したファイルは内容を自分で書いた。
_READ_TOOL_NAMES = {'view', *_tool_calls['EDIT_TOOL_NAMES']}
# パッチで内容を書いたファイル。削除したファイルは記録しない。移動元は適用後に存在しないため、記録の前に除かれる。
_PATCH_OPERATIONS = frozenset({'Add File', 'Update File', 'Move to'})


def _get_read_paths(tool_name, tool_args):
    if tool_name == 'apply_patch':
        return _edit_tool_changes['get_edited_paths'](tool_name, tool_args, _PATCH_OPERATIONS)
    # 閲覧ツールも、対象のファイルのパスを編集ツールと同じ引数名（`path`、`create` では `file_path`）で渡す。
    path = _edit_tool_changes['get_path_argument'](tool_args)
    return [path] if path is not None else []


def main():
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        # 拒否側のpreToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            _utilities_directory, 'known-files-state', 'known_files_state.py',
        ))

        hook_input = _hook_input['read_hook_input']()

        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        tool_name, tool_args = _tool_calls['parse_tool_call'](tool_name, tool_args)
        if tool_name not in _READ_TOOL_NAMES:
            return 0
        paths = {
            state['normalize_path'](cwd, path) for path in _get_read_paths(tool_name, tool_args)
        }
        # ディレクトリの一覧表示や、消えたファイルは記録しない。
        keys = sorted(state['get_file_key'](path) for path in paths if os.path.isfile(path))
        if not keys:
            return 0

        with state['open_state'](state_path) as connection:
            connection.executemany(
                'INSERT INTO files (file, state) VALUES (?, ?) ON CONFLICT(file) DO UPDATE SET state = excluded.state',
                [(key, state['SEEN']) for key in keys],
            )
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to record seen files: {ex}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
