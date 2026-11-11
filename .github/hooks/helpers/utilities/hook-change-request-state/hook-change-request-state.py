import json
import os
import runpy
from datetime import datetime, timezone


# 状態の保存先と書き込みは共通のutilityで行う。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_state_path_utility = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))
_write_json_atomically = runpy.run_path(os.path.join(
    _utilities_directory, 'write-json-atomically', 'write-json-atomically.py',
))['write_json_atomically']


# ユーザーがこのセッションでhookの変更に触れたかの状態。
# 記録するuserPromptSubmitted hook（record-hook-change-requests）と、hookのファイルの変更を拒否する
# preToolUse hook（block-hook-tampering）が共有する。
# プロンプトの内容は保存せず、変更を認めたかどうかだけを保存する。


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
    return _state_path_utility['get_state_path']('hook-change-requests', session_id, '.json')


def is_permitted(state_path):
    # 状態ファイルがなければ、hookの変更を認めていないとみなす。
    if not os.path.isfile(state_path):
        return False
    with open(state_path, encoding='utf-8-sig') as file:
        state = json.load(file)
    if not isinstance(state, dict):
        raise ValueError('hook change request state must be a JSON object.')
    # permittedは真偽値のtrueのみ有効とし、不明な値で変更を認めない。
    return state.get('permitted') is True


def save_permitted(state_path):
    # 一時ファイルに書いてから置き換え、読む側のblock-hook-tamperingに書き込み途中の状態ファイルを読ませない。
    _write_json_atomically(state_path, {'updatedAt': datetime.now(timezone.utc).isoformat(), 'permitted': True})
