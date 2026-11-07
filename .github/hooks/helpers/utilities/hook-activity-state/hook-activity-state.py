import os
import runpy
import sqlite3
from contextlib import closing


# 状態の保存先は共通のutilityで決める。utilityのフォルダーの親がutilitiesである。
_state_path_utility = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get-state-path', 'get-state-path.py',
))


# hookの起動記録。
# 記録するhookランナー（helpers/runner/run-hook.py）と、機能したhookをフッターに出すagentStop hook（debug-add-response-footer）が共有する。
# 機能した（fired）とは、終了コードが0以外か、標準出力が空・空白・空のJSONオブジェクト（{}）以外だったことを指す。


def _get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
    return _state_path_utility['get_state_path']('hook-activity', session_id)


def _connect(state_path):
    # 同じイベントのhookは並列に起動されるため、SQLiteのロック待ちで書き込みを取りこぼさない。
    return closing(sqlite3.connect(state_path, timeout=3))


def record_hook_run(session_id, event_name, hook_name, started_at_ms, fired):
    state_path = _get_state_path(session_id)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with _connect(state_path) as connection, connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS hook_runs ('
            'started_at_ms INTEGER NOT NULL, '
            'event_name TEXT NOT NULL, '
            'hook_name TEXT NOT NULL, '
            'fired INTEGER NOT NULL CHECK (fired IN (0, 1)))'
        )
        connection.execute(
            'INSERT INTO hook_runs (started_at_ms, event_name, hook_name, fired) VALUES (?, ?, ?, ?)',
            (started_at_ms, event_name, hook_name, 1 if fired else 0),
        )


def get_fired_hooks(session_id, since_ms, excluded_events):
    # since_ms以降に起動して機能したhook名を、重複なしの名前順で返す。
    state_path = _get_state_path(session_id)
    if not os.path.isfile(state_path):
        return []
    excluded_events = tuple(excluded_events)
    placeholders = ', '.join('?' for _ in excluded_events)
    event_condition = f' AND event_name NOT IN ({placeholders})' if excluded_events else ''
    with _connect(state_path) as connection:
        rows = connection.execute(
            'SELECT DISTINCT hook_name FROM hook_runs '
            f'WHERE fired = 1 AND started_at_ms >= ?{event_condition} ORDER BY hook_name',
            (since_ms, *excluded_events),
        ).fetchall()
    return [hook_name for (hook_name,) in rows]
