import os
import tempfile
import uuid

# hookが実行をまたいで保持する状態の保存先を作る。状態を持つhookとutilityが共有する。


def parse_session_id(session_id):
    # 入力のsessionIdをUUIDとして解析する。保存先のパスに使うため、UUIDでない値は受け付けない。
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('sessionId must be a non-empty UUID string.')
    try:
        return uuid.UUID(session_id.strip())
    except ValueError:
        raise ValueError('sessionId must be a valid UUID.') from None


def get_state_path(state_name, session_id, extension='.sqlite3'):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。状態ファイル自体は自動削除しない。
    return os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', state_name,
        f'{parse_session_id(session_id)}{extension}',
    )
