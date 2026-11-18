import os
import runpy

# 状態ファイルを開く処理とダイジェストの作成は、共通のutilityで行う。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_open_state_database = runpy.run_path(os.path.join(
    _utilities_directory, 'get-state-path', 'get-state-path.py',
))['open_state_database']
_get_path_sha256_digest = runpy.run_path(os.path.join(
    _utilities_directory, 'get-sha256-digest', 'get-sha256-digest.py',
))['get_path_sha256_digest']

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


def is_first_time_file_denial(state_path, path):
    # 戻り値は、このファイルをセッションで初めて拒否するか。初めてなら記録し、以降の呼び出しではFalseを返す。
    # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
    return bool(filter_first_time_denials(state_path, [(_get_path_sha256_digest(path), path)]))
