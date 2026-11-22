import os
import sqlite3
import sys


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'write_hook_output'),
    os.path.join(_utilities_directory, 'edit_tool_changes'),
    os.path.join(_utilities_directory, 'get_display_path'),
]
import read_hook_input as _hook_input
import write_hook_output as _hook_output
from edit_tool_changes import get_edited_paths as _get_edited_paths
from get_display_path import get_display_path as _get_display_path


# パッチで既存の内容を書き換えるファイル。追加・削除・移動先は内容を把握する必要がないため対象外とする。
_PATCH_OPERATIONS = frozenset({'Update File'})
_DENY_REASON = (
    'Edit denied: you have not read the following existing files in this session: {files}. '
    'Editing from memory or assumptions risks failed replacements and broken surrounding code. '
    'View the relevant part of each file first, then redo the edit based on its current content.'
)


def _deny_unread_files(state, state_path, paths):
    # paths：(正規化したパス, 表示用のパス) のリスト。戻り値は、今回拒否するファイルの表示用のパスのリスト。
    # 同じファイルの拒否は1回だけにし、読めない事情があっても編集を続けられるようにする。
    denied_paths = []
    with state.open_state(state_path) as connection:
        for normalized_path, path in paths:
            key = state.get_file_key(normalized_path)
            if connection.execute('SELECT 1 FROM files WHERE file = ?', (key,)).fetchone():
                continue
            connection.execute('INSERT INTO files (file, state) VALUES (?, ?)', (key, state.DENIED))
            denied_paths.append(path)
    return denied_paths


def main():
    _hook_output.configure_output_streams()

    try:
        # 記録側のpostToolUse hookと状態の形式・保存先を共有する。
        sys.path.insert(0, os.path.join(_utilities_directory, 'known_files_state'))
        import known_files_state as state

        hook_input = _hook_input.read_hook_input()

        state_path = state.get_state_path(hook_input.get('sessionId'))
        cwd = _hook_input.get_cwd(hook_input)
        tool_name, tool_args = _hook_input.get_tool_call(hook_input)

        # 照合には正規化したパス、表示には正規化前のパスを使う。
        paths = {}
        for path in _get_edited_paths(tool_name, tool_args, _PATCH_OPERATIONS):
            paths.setdefault(state.normalize_path(cwd, path), os.path.realpath(os.path.join(cwd, path)))
        # 新規作成と、存在しないファイルへの編集（ツール側で失敗する）は対象外とする。
        existing_paths = sorted(item for item in paths.items() if os.path.isfile(item[1]))
        if not existing_paths:
            return 0
        denied_paths = _deny_unread_files(state, state_path, existing_paths)
    except (OSError, UnicodeError, ValueError, ImportError, sqlite3.Error) as ex:
        print(f'Failed to check read-before-edit: {ex}', file=sys.stderr)
        return 2

    if denied_paths:
        real_cwd = os.path.realpath(cwd)
        files = ', '.join(_get_display_path(path, real_cwd) for path in denied_paths)
        # 正常な拒否なので終了コードは0。
        _hook_output.write_deny_decision(_DENY_REASON.format(files=files))
    return 0


if __name__ == '__main__':
    sys.exit(main())
