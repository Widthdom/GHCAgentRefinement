import os
import sqlite3
import tempfile
import uuid
from contextlib import closing, contextmanager

# hookが実行をまたいで保持する状態の保存先を作り、SQLiteの状態ファイルを開く。状態を持つhookとutilityが共有する。

# 同じイベントのhookやツール呼び出しは並列に動くため、SQLiteのロックを待つ秒数。
_LOCK_TIMEOUT_SECONDS = 3


def parse_session_id(session_id):
    # 入力のsessionIdをUUIDとして解析する。保存先のパスに使うため、UUIDでない値は受け付けない。
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('sessionId must be a non-empty UUID string.')
    try:
        return uuid.UUID(session_id.strip())
    except ValueError:
        raise ValueError('sessionId must be a valid UUID.') from None


def get_state_path(state_name, session_id, extension='.sqlite3'):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。状態ファイル自体は自動削除しない。
    return os.path.join(
        tempfile.gettempdir(), 'ghc_agent_refinement', 'hook_states', state_name,
        f'{parse_session_id(session_id)}{extension}',
    )


@contextmanager
def open_state_database(state_path, *table_definitions):
    # SQLiteの状態ファイルを開き、`BEGIN IMMEDIATE` で書き込みのトランザクションを始めた接続を返す。
    # table_definitions：`CREATE TABLE IF NOT EXISTS` の文。保存先のフォルダーと表がなければ作る。
    # トランザクションで、並列のツール呼び出しでも状態の更新を取りこぼさない。withを抜けるときに確定し、例外なら取り消す。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with closing(sqlite3.connect(state_path, timeout=_LOCK_TIMEOUT_SECONDS)) as connection:
        for table_definition in table_definitions:
            connection.execute(table_definition)
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            yield connection


def read_state_database(state_path, query, parameters=()):
    # 状態を読むだけの問い合わせの結果の行（タプル）のリストを返す。状態ファイルや表がまだなければ、記録がないとみなして空を返す。
    if not os.path.isfile(state_path):
        return []
    with closing(sqlite3.connect(state_path, timeout=_LOCK_TIMEOUT_SECONDS)) as connection:
        try:
            return connection.execute(query, parameters).fetchall()
        except sqlite3.OperationalError:
            return []
