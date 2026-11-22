import os
import sys


# 状態の保存先は共通のutilityで決める。utilityのフォルダーの親がutilitiesである。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get_state_path'))
import get_state_path as _state_path_utility


# エージェントがメッセージを読んだコミットの状態。
# 記録するpostToolUse hook（record_viewed_commits）と、修正コミット由来の行を読まずに消す編集を拒否する
# preToolUse hook（block_regression_of_past_fixes）が共有する。コミットIDは小文字の完全な形で保存する。


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
    return _state_path_utility.get_state_path('viewed_commits', session_id)


def record_viewed(state_path, commit_ids):
    with _state_path_utility.open_state_database(
        state_path, 'CREATE TABLE IF NOT EXISTS commits (id TEXT PRIMARY KEY)',
    ) as connection:
        connection.executemany(
            'INSERT OR IGNORE INTO commits (id) VALUES (?)', [(commit_id.lower(),) for commit_id in commit_ids],
        )


def get_viewed(state_path):
    # 戻り値はコミットIDの集合。状態ファイルがなければ空。
    return {commit_id for (commit_id,) in _state_path_utility.read_state_database(state_path, 'SELECT id FROM commits')}
