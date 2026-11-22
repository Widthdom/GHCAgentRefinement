import os
import sys


# 状態の保存先と照合用のダイジェストは共通のutilityで作る。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_utilities_directory, 'get_state_path'))
import get_state_path as _state_path_utility
# 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
sys.path.insert(0, os.path.join(_utilities_directory, 'get_sha256_digest'))
from get_sha256_digest import get_path_sha256_digest as get_file_key


# エージェントが内容を把握したファイル（known files）の状態。
# 記録するpostToolUse hook（record_seen_files）と、把握せずに編集するのを拒否するpreToolUse hook（require_read_before_edit）、
# 把握せずに中身を推測で語る応答を差し戻すagentStop hook（detect_unread_speculation）が共有する。
# 各ファイルの状態は次のいずれか。行がないファイルは、まだ読んでいない。
#   seen：閲覧または編集に成功したことがある。
#   denied：読まずに編集しようとして1回拒否した。
SEEN = 'seen'
DENIED = 'denied'


def get_state_path(session_id):
    return _state_path_utility.get_state_path('known_files', session_id)


def normalize_path(cwd, path):
    # 相対パスはcwd基準で解決し、同じファイルを別表記でも同一と判定する。
    return os.path.normcase(os.path.realpath(os.path.join(cwd, path)))


def open_state(state_path):
    # 書き込みのトランザクションを始めた状態の接続を返す。
    return _state_path_utility.open_state_database(
        state_path,
        f"CREATE TABLE IF NOT EXISTS files (file TEXT PRIMARY KEY, state TEXT NOT NULL CHECK (state IN ('{SEEN}', '{DENIED}')))",
    )
