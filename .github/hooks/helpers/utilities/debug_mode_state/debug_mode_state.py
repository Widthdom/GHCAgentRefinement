import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone

# デバッグモードの状態と、デバッグ専用hookのログの保存先を扱う。
# 状態を書き込むset_debug_mode hookと、状態を読むデバッグ専用hook、ログを書くhook・探すhookが共有する。

# 状態の保存先とセッションIDの検証、状態ファイルの書き込みは共通のutilityで行う。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [
    os.path.join(_utilities_directory, 'get_state_path'),
    os.path.join(_utilities_directory, 'write_json_atomically'),
]
import get_state_path as _state_path_utility
from write_json_atomically import write_json_atomically as _write_json_atomically


def get_debug_mode_path(session_id):
    # session_id：sessionIdの値、または解析済みのUUID。不正なセッションIDはValueErrorにする。
    if isinstance(session_id, uuid.UUID):
        session_id = str(session_id)
    return _state_path_utility.get_state_path('debug-mode', session_id, '.json')


def save_debug_mode(state_path, enabled):
    # 状態は {"updatedAt": 更新日時（UTC、ISO 8601）, "enabled": 有効か} の形で保存する。
    # 一時ファイルに書いてから置き換え、読む側のhookに書き込み途中の状態ファイルを読ませない。
    _write_json_atomically(state_path, {'updatedAt': datetime.now(timezone.utc).isoformat(), 'enabled': enabled})


def get_debug_mode(session_id):
    # session_id：sessionIdの文字列、または解析済みのUUID。状態ファイルがなければ無効とする。
    state_path = get_debug_mode_path(session_id)
    if not os.path.isfile(state_path):
        return False
    with open(state_path, encoding='utf-8-sig') as file:
        debug_state = json.load(file)
    if not isinstance(debug_state, dict):
        raise ValueError('Expected a JSON object.')
    # enabledは真偽値のtrueのみ有効とし、不明な値で通常対話を妨げない。
    return debug_state.get('enabled') is True


def get_debug_log_directory(log_name, session_id):
    # デバッグ専用hookのログは、セッションUUID別のフォルダーに保存する。フォルダーは作らず、ログ自体も自動削除しない。
    return os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'debug-logs', log_name,
        str(_state_path_utility.parse_session_id(session_id)),
    )
