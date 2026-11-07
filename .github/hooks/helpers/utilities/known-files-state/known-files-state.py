import hashlib
import os
import runpy
import sqlite3
from contextlib import closing, contextmanager


# 状態の保存先は共通のutilityで決める。utilityのフォルダーの親がutilitiesである。
_state_path_utility = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get-state-path', 'get-state-path.py',
))


# エージェントが内容を把握したファイル（known files）の状態。
# 記録するpostToolUse hook（record-seen-files）と、把握せずに編集するのを拒否するpreToolUse hook（require-read-before-edit）、
# 把握せずに中身を推測で語る応答を差し戻すagentStop hook（detect-unread-speculation）が共有する。
# 各ファイルの状態は次のいずれか。行がないファイルは、まだ読んでいない。
#   seen：閲覧または編集に成功したことがある。
#   denied：読まずに編集しようとして1回拒否した。
SEEN = 'seen'
DENIED = 'denied'


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
    return _state_path_utility['get_state_path']('known-files', session_id)


def normalize_path(cwd, path):
    # 相対パスはcwd基準で解決し、同じファイルを別表記でも同一と判定する。
    return os.path.normcase(os.path.realpath(os.path.join(cwd, path)))


def get_file_key(path):
    # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
    return hashlib.sha256(path.encode('utf-8')).hexdigest()


@contextmanager
def open_state(state_path):
    # SQLiteのトランザクションで、並列のツール呼び出しでも状態の更新を取りこぼさない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS files ('
            'file TEXT PRIMARY KEY, '
            f"state TEXT NOT NULL CHECK (state IN ('{SEEN}', '{DENIED}')))"
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
