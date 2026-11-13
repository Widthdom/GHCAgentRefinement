import os
import runpy
import tempfile

# デバッグ専用hookのログの保存先を作る。ログを書くhookと、書かれたログを探すhookが共有する。

# セッションIDの検証は共通のutilityで行う。utilityのフォルダーの親がutilitiesである。
_parse_session_id = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get-state-path', 'get-state-path.py',
))['parse_session_id']


def get_debug_log_directory(log_name, session_id):
    # ログはセッションUUID別のフォルダーに保存する。フォルダーは作らず、ログ自体も自動削除しない。
    return os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'debug-logs', log_name, str(_parse_session_id(session_id)),
    )
