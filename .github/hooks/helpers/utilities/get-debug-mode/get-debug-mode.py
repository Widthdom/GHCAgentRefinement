import json
import os
import runpy

# 状態の保存先は共通のutilityで決める。utilityのフォルダーの親がutilitiesである。
_get_state_path = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get-state-path', 'get-state-path.py',
))['get_state_path']


def get_debug_mode(session_id):
    # session_id：sessionIdの文字列、または解析済みのUUID。状態はset-debug-mode hookが書き込む。
    debug_state_file_path = _get_state_path('debug-mode', str(session_id), '.json')
    if not os.path.isfile(debug_state_file_path):
        return False

    with open(debug_state_file_path, encoding='utf-8-sig') as file:
        debug_state = json.load(file)
    if not isinstance(debug_state, dict):
        raise ValueError('Expected a JSON object.')
    # enabledは真偽値のtrueのみ有効とし、不明な値で通常対話を妨げない。
    return debug_state.get('enabled') is True
