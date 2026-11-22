import hashlib
import io
import os
import sqlite3
import sys
import time
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'write_hook_output'),
    os.path.join(_utilities_directory, 'parse_tool_calls'),
    os.path.join(_utilities_directory, 'get_sha256_digest'),
    os.path.join(_utilities_directory, 'get_state_path'),
]
import read_hook_input as _hook_input
import write_hook_output as _hook_output
import parse_tool_calls as _tool_calls
from get_sha256_digest import get_path_sha256_digest as _get_path_sha256_digest
import get_state_path as _state_path_utility
_get_state_path = _state_path_utility.get_state_path
sys.path[:0] = [
    os.path.join(_utilities_directory, 'edit_tool_changes'),
    os.path.join(_utilities_directory, 'get_display_path'),
]
from edit_tool_changes import get_edited_paths as _get_edited_paths
from get_display_path import get_display_path as _get_display_path


_WINDOW_SECONDS = 600
# 削除済み・ファイル以外を表す内容ハッシュ。SHA-256の16進表記とは衝突しない。
_MISSING_CONTENT = 'missing'
_OSCILLATION_CONTEXT = (
    'The following files have just returned to content they already had earlier '
    'within the last ten minutes, after being changed in between: {files}. '
    'You may be oscillating between alternative edits instead of making progress. '
    'Before editing these files again, briefly state which versions you have tried, '
    'why each was rejected, and what evidence will decide between them. '
    'Do not switch back and forth again without new evidence. If the user explicitly '
    'asked for this revert, continue as requested. If you cannot decide, ask the user.'
)


def _get_content_hash(path):
    if not os.path.isfile(path):
        return _MISSING_CONTENT
    with open(path, 'rb') as file:
        return hashlib.sha256(file.read()).hexdigest()


def _record_edits(state_path, edits):
    # 戻り値は、変更を挟んで以前と同じ内容に戻ったファイルパスのリスト。
    oscillated_paths = []
    # SQLiteのトランザクションで、並列に編集しても履歴の更新を取りこぼさない。
    with _state_path_utility.open_state_database(
        state_path,
        'CREATE TABLE IF NOT EXISTS contents ('
        'file TEXT NOT NULL, '
        'content TEXT NOT NULL, '
        'last_seen REAL NOT NULL, '
        'sequence INTEGER NOT NULL, '
        'notified INTEGER NOT NULL CHECK (notified IN (0, 1)), '
        'PRIMARY KEY (file, content))',
    ) as connection:
        # ロック待ちの後に時刻を取り、並列処理の記録を「未来」として消さない。
        now = time.time()
        # 古い内容と、時計が巻き戻った場合の未来の記録は引き継がない。
        connection.execute(
            'DELETE FROM contents WHERE last_seen < ? OR last_seen > ?',
            (now - _WINDOW_SECONDS, now),
        )
        # 内容の新旧はファイルをまたいだ連番で決める。同時刻の記録でも順序が崩れない。
        sequence = connection.execute('SELECT COALESCE(MAX(sequence), 0) + 1 FROM contents').fetchone()[0]
        for path, file_key, content in edits:
            latest = connection.execute(
                'SELECT content FROM contents WHERE file = ? ORDER BY sequence DESC LIMIT 1', (file_key,),
            ).fetchone()
            # 内容が変わらない書き込みは往復ではないため、履歴を更新しない。
            if latest and latest[0] == content:
                continue
            previous = connection.execute(
                'SELECT notified FROM contents WHERE file = ? AND content = ?', (file_key, content),
            ).fetchone()
            # 通知時はそのファイルの既存の内容をすべて通知済みにし、以降の同じ往復で助言を繰り返さない。
            # 新しい内容を経由して再び往復した場合だけ、改めて通知する。
            notified = 1 if previous else 0
            if previous and not previous[0]:
                oscillated_paths.append(path)
                connection.execute('UPDATE contents SET notified = 1 WHERE file = ?', (file_key,))
            connection.execute(
                'INSERT INTO contents (file, content, last_seen, sequence, notified) VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT(file, content) DO UPDATE SET '
                'last_seen = excluded.last_seen, sequence = excluded.sequence, notified = excluded.notified',
                (file_key, content, now, sequence, notified),
            )
            sequence += 1
    return oscillated_paths


def main():
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input.read_hook_input()

        state_path = _get_state_path('edit-oscillation', hook_input.get('sessionId'))

        cwd = _hook_input.get_cwd(hook_input)
        tool_name, tool_args = _hook_input.get_tool_call(hook_input)

        if not _tool_calls.is_edit_tool(tool_name):
            return 0
        # パッチの削除・移動元も、削除扱いで含める。
        # 相対パスはcwd基準で解決し、同じファイルを別表記でも同一と判定する。normcaseは照合だけに使い、表示には正規化前のパスを使う。
        paths = {}
        for path in _get_edited_paths(tool_name, tool_args):
            real_path = os.path.realpath(os.path.join(cwd, path))
            paths.setdefault(os.path.normcase(real_path), real_path)
        if not paths:
            return 0

        # 編集後のファイル内容を読む。パスと内容は保存せず、比較用のSHA-256だけを保存する。
        edits = [
            (path, _get_path_sha256_digest(normalized_path), _get_content_hash(path))
            for normalized_path, path in sorted(paths.items())
        ]

        # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
        # 期限切れレコードは次回編集時に削除する。DBファイル自体は自動削除しない。
        oscillated_paths = _record_edits(state_path, edits)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to detect edit oscillation: {ex}', file=sys.stderr)
        return 2

    if oscillated_paths:
        cwd = os.path.realpath(cwd)
        files = ', '.join(_get_display_path(path, cwd) for path in oscillated_paths)
        # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
        _hook_output.write_additional_context(_OSCILLATION_CONTEXT.format(files=files))
    return 0


if __name__ == '__main__':
    sys.exit(main())
