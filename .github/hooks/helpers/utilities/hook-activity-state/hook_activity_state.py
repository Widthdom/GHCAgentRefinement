import os
import sys


# 状態の保存先は共通のutilityで決める。utilityのフォルダーの親がutilitiesである。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get-state-path'))
import get_state_path as _state_path_utility


# hookの起動記録。
# 記録するhookランナー（helpers/runner/run_hook.py）と、機能したhookをフッターに出すagentStop hook（debug-add-response-footer）が共有する。
# 機能した（fired）とは、終了コードが0以外か、標準出力が空・空白・空のJSONオブジェクト（{}）以外だったことを指す。


def _get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
    return _state_path_utility.get_state_path('hook-activity', session_id)


def record_hook_run(session_id, event_name, hook_name, started_at_ms, fired):
    # 同じイベントのhookは並列に起動されるため、トランザクションで書き込みを取りこぼさない。
    with _state_path_utility.open_state_database(
        _get_state_path(session_id),
        'CREATE TABLE IF NOT EXISTS hook_runs ('
        'started_at_ms INTEGER NOT NULL, '
        'event_name TEXT NOT NULL, '
        'hook_name TEXT NOT NULL, '
        'fired INTEGER NOT NULL CHECK (fired IN (0, 1)))',
    ) as connection:
        connection.execute(
            'INSERT INTO hook_runs (started_at_ms, event_name, hook_name, fired) VALUES (?, ?, ?, ?)',
            (started_at_ms, event_name, hook_name, 1 if fired else 0),
        )


def get_fired_hooks(session_id, since_ms, excluded_events):
    # since_ms以降に起動して機能したhook名を、重複なしの名前順で返す。
    excluded_events = tuple(excluded_events)
    placeholders = ', '.join('?' for _ in excluded_events)
    event_condition = f' AND event_name NOT IN ({placeholders})' if excluded_events else ''
    rows = _state_path_utility.read_state_database(
        _get_state_path(session_id),
        'SELECT DISTINCT hook_name FROM hook_runs '
        f'WHERE fired = 1 AND started_at_ms >= ?{event_condition} ORDER BY hook_name',
        (since_ms, *excluded_events),
    )
    return [hook_name for (hook_name,) in rows]
