import os
import sqlite3
import tempfile
import uuid
from contextlib import closing


# エージェントがメッセージと差分を読んだコミットの状態。
# 記録するpostToolUse hook（record-viewed-commits）と、修正コミット由来の行を読まずに消す編集を拒否する
# preToolUse hook（block-regression-of-past-fixes）が共有する。コミットIDは小文字の完全な形で保存する。


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。DBファイル自体は自動削除しない。
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('sessionId must be a non-empty UUID string.')
    try:
        parsed_session_id = uuid.UUID(session_id.strip())
    except ValueError:
        raise ValueError('sessionId must be a valid UUID.') from None
    return os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'viewed-commits', f'{parsed_session_id}.sqlite3',
    )


def record_viewed(state_path, commit_ids):
    # SQLiteのトランザクションで、並列のツール呼び出しでも記録を取りこぼさない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS commits (id TEXT PRIMARY KEY)')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.executemany(
                'INSERT OR IGNORE INTO commits (id) VALUES (?)', [(commit_id.lower(),) for commit_id in commit_ids],
            )


def get_viewed(state_path):
    # 戻り値はコミットIDの集合。状態ファイルがなければ空。
    if not os.path.isfile(state_path):
        return set()
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        try:
            return {row[0] for row in connection.execute('SELECT id FROM commits')}
        except sqlite3.OperationalError:
            return set()
