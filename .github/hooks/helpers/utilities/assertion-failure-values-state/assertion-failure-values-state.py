import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import closing, contextmanager


# 最新のテスト実行で失敗したアサーションの、期待値と実際の値の組の状態。
# 記録するpostToolUse hook（record-test-failure-values）と、期待値を実際の値に書き換える編集を拒否する
# preToolUse hook（block-goalpost-moving）、テストを通すために期待値を実装へハードコードする編集を拒否する
# preToolUse hook（block-hardcoding-for-tests）が共有する。
# 値そのものは保存せず、正規化した (期待値, 実際の値) の組と期待値の照合用SHA-256だけを保存する。
MAX_VALUE_LENGTH = 200
QUOTES = ('"', "'", '`')


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。DBファイル自体は自動削除しない。
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('sessionId must be a non-empty UUID string.')
    try:
        parsed_session_id = uuid.UUID(session_id.strip())
    except ValueError:
        raise ValueError('sessionId must be a valid UUID.') from None
    return os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'test-failure-values',
        f'{parsed_session_id}.sqlite3',
    )


def normalize_value(value):
    # テストの出力とソースコードのリテラルを同じ形で比べられるよう、前後の空白・末尾のカンマ・1組の引用符を除く。
    # 比較できない値（空、複数行、長すぎる値）はNoneを返す。
    value = value.strip().removesuffix(',').strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
        value = value[1:-1]
    if not value or '\n' in value or len(value) > MAX_VALUE_LENGTH:
        return None
    return value


def get_pair_key(expected, actual):
    return hashlib.sha256(
        json.dumps([expected, actual], ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
    ).hexdigest()


def get_value_key(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def get_file_key(path):
    # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
    return hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()


@contextmanager
def open_state(state_path):
    # SQLiteのトランザクションで、並列のツール呼び出しでも状態の更新を取りこぼさない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    #   pairs：最新のテスト実行で失敗した (期待値, 実際の値) の組。
    #   expected_values：pairsの期待値。期待値の側が決まらない形式では両方の値。
    #   denials：期待値の書き換えを1回拒否したファイル（block-goalpost-moving）。
    #   hardcoding_denials：期待値のハードコードを1回拒否したファイル（block-hardcoding-for-tests）。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS pairs (pair TEXT PRIMARY KEY)')
        connection.execute('CREATE TABLE IF NOT EXISTS expected_values (value TEXT PRIMARY KEY)')
        connection.execute('CREATE TABLE IF NOT EXISTS denials (file TEXT PRIMARY KEY)')
        connection.execute('CREATE TABLE IF NOT EXISTS hardcoding_denials (file TEXT PRIMARY KEY)')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
