import os
import sqlite3
from contextlib import closing

# 検出した問題を、対象ごとにセッションでまだ通知していないものがある場合だけに絞る。
# 解消した問題は記録を消し、再び現れたときに改めて通知する。通知した問題をSQLiteに記録する。
# 編集後のファイルを検査して助言するpostToolUse hookが共有する。
_TABLE = 'notified_problems'


def filter_new_notifications(state_path, results):
    # state_path：hookごと・セッションごとのSQLiteファイルのパス。親ディレクトリがなければ作る。
    # results：(キー, 値, 問題のリスト) の組のリスト。キーは対象を表す文字列（ファイルのSHA-256など）、問題は文字列。
    # 戻り値は、まだ通知していない問題を1つ以上含む組の値のリスト。キーごとの記録は、今回の問題のリストに置き換える。
    # SQLiteのトランザクションで、並列のツール呼び出しでも同じ問題を重複して通知しない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    values = []
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            f'CREATE TABLE IF NOT EXISTS {_TABLE} (key TEXT NOT NULL, problem TEXT NOT NULL, PRIMARY KEY (key, problem))'
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for key, value, problems in results:
                stored = connection.execute(f'SELECT problem FROM {_TABLE} WHERE key = ?', (key,)).fetchall()
                for (problem,) in stored:
                    if problem not in problems:
                        connection.execute(f'DELETE FROM {_TABLE} WHERE key = ? AND problem = ?', (key, problem))
                inserted = [
                    problem for problem in problems
                    if connection.execute(
                        f'INSERT OR IGNORE INTO {_TABLE} (key, problem) VALUES (?, ?)', (key, problem),
                    ).rowcount
                ]
                if inserted:
                    values.append(value)
    return values
