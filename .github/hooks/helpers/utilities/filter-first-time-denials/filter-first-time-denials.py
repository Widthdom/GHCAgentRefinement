import os
import sqlite3
from contextlib import closing

# 拒否の候補を、対象ごとにセッションで初めての拒否だけに絞る。拒否は対象ごとに1回だけにし、意図した操作なら再実行で通す。
# 絞り込みのため、拒否した対象をSQLiteに記録する。拒否hook（preToolUse）が共有する。
_TABLE = 'first_denials'


def filter_first_time_denials(state_path, candidates):
    # state_path：hookごと・セッションごとのSQLiteファイルのパス。親ディレクトリがなければ作る。
    # candidates：(キー, 値) の組のリスト。キーは拒否の対象を表す文字列（ファイルのSHA-256など）。
    # 戻り値は、セッションで初めて拒否するキーの値のリスト。返したキーは記録し、以降の呼び出しでは返さない。
    # 同じキーが候補に重複した場合は、最初の組の値だけを返す。
    # SQLiteのトランザクションで、並列のツール呼び出しでも重複して拒否しない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    values = []
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(f'CREATE TABLE IF NOT EXISTS {_TABLE} (key TEXT PRIMARY KEY)')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for key, value in candidates:
                if connection.execute(f'INSERT OR IGNORE INTO {_TABLE} (key) VALUES (?)', (key,)).rowcount:
                    values.append(value)
    return values
