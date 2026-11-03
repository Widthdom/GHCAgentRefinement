import json
import os
import tempfile


def get_debug_mode(session_id):
    debug_state_file_path = os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'debug-mode', f'{session_id}.json',
    )
    if not os.path.isfile(debug_state_file_path):
        return False

    with open(debug_state_file_path, encoding='utf-8-sig') as file:
        debug_state = json.load(file)
    if not isinstance(debug_state, dict):
        raise ValueError('Expected a JSON object.')
    # enabledは真偽値のtrueのみ有効とし、不明な値で通常対話を妨げない。
    return debug_state.get('enabled') is True
