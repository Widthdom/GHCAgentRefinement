import os
import runpy

# 状態ファイルは共通のutilityで開く。utilityのフォルダーの親がutilitiesである。
_open_state_database = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get-state-path', 'get-state-path.py',
))['open_state_database']

# 拒否の候補を、対象ごとにセッションで初めての拒否だけに絞る。拒否は対象ごとに1回だけにし、意図した操作なら再実行で通す。
# 絞り込みのため、拒否した対象をSQLiteに記録する。拒否hook（preToolUse）が共有する。
_TABLE = 'first_denials'


def filter_first_time_denials(state_path, candidates):
    # state_path：hookごと・セッションごとのSQLiteファイルのパス。親ディレクトリがなければ作る。
    # candidates：(キー, 値) の組のリスト。キーは拒否の対象を表す文字列（ファイルのSHA-256など）。
    # 戻り値は、セッションで初めて拒否するキーの値のリスト。返したキーは記録し、以降の呼び出しでは返さない。
    # 同じキーが候補に重複した場合は、最初の組の値だけを返す。
    # SQLiteのトランザクションで、並列のツール呼び出しでも重複して拒否しない。
    values = []
    with _open_state_database(state_path, f'CREATE TABLE IF NOT EXISTS {_TABLE} (key TEXT PRIMARY KEY)') as connection:
        for key, value in candidates:
            if connection.execute(f'INSERT OR IGNORE INTO {_TABLE} (key) VALUES (?)', (key,)).rowcount:
                values.append(value)
    return values
