import hashlib
import os
import sqlite3
import tempfile
import uuid
from contextlib import closing, contextmanager


# エージェントが内容を把握したファイル（known files）の状態。
# 記録するpostToolUse hook（record-seen-files）と、把握せずに編集するのを拒否するpreToolUse hook（require-read-before-edit）、
# 把握せずに中身を推測で語る応答を差し戻すagentStop hook（detect-unread-speculation）が共有する。
# 各ファイルの状態は次のいずれか。行がないファイルは、まだ読んでいない。
#   seen：閲覧または編集に成功したことがある。
#   denied：読まずに編集しようとして1回拒否した。
SEEN = 'seen'
DENIED = 'denied'


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。DBファイル自体は自動削除しない。
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('sessionId must be a non-empty UUID string.')
    try:
        parsed_session_id = uuid.UUID(session_id.strip())
    except ValueError:
        raise ValueError('sessionId must be a valid UUID.') from None
    return os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'known-files', f'{parsed_session_id}.sqlite3',
    )


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
