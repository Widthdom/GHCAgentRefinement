import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import closing, contextmanager


# レジストリなどで実在を確かめた依存パッケージの状態。
# 記録するpostToolUse hook（record-verified-packages）と、実在を確かめられない依存の追加を拒否する
# preToolUse hook（block-unverified-dependencies）が共有する。
# エコシステムは次のいずれか。名前はエコシステムごとの規則で正規化して保存する。
#   nuget：NuGetのパッケージID。
#   pypi：PyPIの配布名。
#   npm：npmのパッケージ名。
#   python-module：Pythonでimportできると確かめたモジュールの最上位の名前。
_NUGET = 'nuget'
_PYPI = 'pypi'
_NPM = 'npm'
_PYTHON_MODULE = 'python-module'
_ECOSYSTEMS = (_NUGET, _PYPI, _NPM, _PYTHON_MODULE)


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。DBファイル自体は自動削除しない。
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('sessionId must be a non-empty UUID string.')
    try:
        parsed_session_id = uuid.UUID(session_id.strip())
    except ValueError:
        raise ValueError('sessionId must be a valid UUID.') from None
    return os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'verified-packages',
        f'{parsed_session_id}.sqlite3',
    )


def normalize_name(ecosystem, name):
    # NuGetとnpmは大文字小文字を区別しない。PyPIはPEP 503の規則で `-`・`_`・`.` の連続を `-` にそろえる。
    if ecosystem == _PYPI:
        return re.sub(r'[-_.]+', '-', name).lower()
    if ecosystem in (_NUGET, _NPM):
        return name.lower()
    return name


@contextmanager
def _open_state(state_path):
    # SQLiteのトランザクションで、並列のツール呼び出しでも状態の更新を取りこぼさない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS verified (ecosystem TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (ecosystem, name))'
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            yield connection


def record_verified(state_path, packages):
    # packages：(エコシステム, 名前) のリスト。
    with _open_state(state_path) as connection:
        connection.executemany(
            'INSERT OR IGNORE INTO verified (ecosystem, name) VALUES (?, ?)',
            [(ecosystem, normalize_name(ecosystem, name)) for ecosystem, name in packages],
        )


def get_verified(state_path):
    # 戻り値は (エコシステム, 正規化した名前) の集合。状態ファイルがなければ空。
    if not os.path.isfile(state_path):
        return set()
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        try:
            return set(connection.execute('SELECT ecosystem, name FROM verified').fetchall())
        except sqlite3.OperationalError:
            return set()
