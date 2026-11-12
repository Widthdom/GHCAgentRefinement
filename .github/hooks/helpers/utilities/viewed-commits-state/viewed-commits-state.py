import os
import runpy
import sqlite3
from contextlib import closing


# 状態の保存先は共通のutilityで決める。utilityのフォルダーの親がutilitiesである。
_state_path_utility = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get-state-path', 'get-state-path.py',
))


# エージェントがメッセージを読んだコミットの状態。
# 記録するpostToolUse hook（record-viewed-commits）と、修正コミット由来の行を読まずに消す編集を拒否する
# preToolUse hook（block-regression-of-past-fixes）が共有する。コミットIDは小文字の完全な形で保存する。


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
    return _state_path_utility['get_state_path']('viewed-commits', session_id)


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
