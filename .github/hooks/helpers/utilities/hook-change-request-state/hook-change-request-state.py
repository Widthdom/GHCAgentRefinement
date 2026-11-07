import json
import os
import runpy
from datetime import datetime, timezone


# 状態の保存先は共通のutilityで決める。utilityのフォルダーの親がutilitiesである。
_state_path_utility = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get-state-path', 'get-state-path.py',
))


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
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, 'w', encoding='utf-8', newline='') as file:
        json.dump(
            {'updatedAt': datetime.now(timezone.utc).isoformat(), 'permitted': True},
            file, ensure_ascii=False, separators=(',', ':'),
        )
