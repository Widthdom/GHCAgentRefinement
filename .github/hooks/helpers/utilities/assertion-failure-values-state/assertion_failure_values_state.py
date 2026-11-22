import json
import os
import runpy


# 状態の保存先と照合用のダイジェストは共通のutilityで作る。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_state_path_utility = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get_state_path.py'))
_sha256_digest = runpy.run_path(os.path.join(_utilities_directory, 'get-sha256-digest', 'get_sha256_digest.py'))
# 状態には値やパスを保存せず、照合用のSHA-256だけを保存する。
get_value_key = _sha256_digest['get_text_sha256_digest']
get_file_key = _sha256_digest['get_path_sha256_digest']


# 最新のテスト実行で失敗したアサーションの、期待値と実際の値の組の状態。
# 記録するpostToolUse hook（record-test-failure-values）と、期待値を実際の値に書き換える編集を拒否する
# preToolUse hook（block-goalpost-moving）、テストを通すために期待値を実装へハードコードする編集を拒否する
# preToolUse hook（block-hardcoding-for-tests）が共有する。
# 値そのものは保存せず、正規化した (期待値, 実際の値) の組と期待値の照合用SHA-256だけを保存する。
_MAX_VALUE_LENGTH = 200
_QUOTES = ('"', "'", '`')


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
    return _state_path_utility['get_state_path']('test-failure-values', session_id)


def normalize_value(value):
    # テストの出力とソースコードのリテラルを同じ形で比べられるよう、前後の空白・末尾のカンマ・1組の引用符を除く。
    # 比較できない値（空、複数行、長すぎる値）はNoneを返す。
    value = value.strip().removesuffix(',').strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        value = value[1:-1]
    if not value or '\n' in value or len(value) > _MAX_VALUE_LENGTH:
        return None
    return value


def get_pair_key(expected, actual):
    return get_value_key(json.dumps([expected, actual], ensure_ascii=False, separators=(',', ':')))


def open_state(state_path):
    # 書き込みのトランザクションを始めた状態の接続を返す。表は次のとおり。
    #   pairs：最新のテスト実行で失敗した (期待値, 実際の値) の組。
    #   expected_values：pairsの期待値。期待値の側が決まらない形式では両方の値。
    #   denials：期待値の書き換えを1回拒否したファイル（block-goalpost-moving）。
    #   hardcoding_denials：期待値のハードコードを1回拒否したファイル（block-hardcoding-for-tests）。
    return _state_path_utility['open_state_database'](
        state_path,
        'CREATE TABLE IF NOT EXISTS pairs (pair TEXT PRIMARY KEY)',
        'CREATE TABLE IF NOT EXISTS expected_values (value TEXT PRIMARY KEY)',
        'CREATE TABLE IF NOT EXISTS denials (file TEXT PRIMARY KEY)',
        'CREATE TABLE IF NOT EXISTS hardcoding_denials (file TEXT PRIMARY KEY)',
    )
