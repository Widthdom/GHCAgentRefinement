import os
import re
import sys


# 状態の保存先は共通のutilityで決める。utilityのフォルダーの親がutilitiesである。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'get_state_path'))
import get_state_path as _state_path_utility


# レジストリなどで実在を確かめた依存パッケージの状態。
# 記録するpostToolUse hook（record_verified_packages）と、実在を確かめられない依存の追加を拒否する
# preToolUse hook（block_unverified_dependencies）が共有する。
# エコシステムは次のいずれか。名前はエコシステムごとの規則で正規化して保存する。
#   nuget：NuGetのパッケージID。
#   pypi：PyPIの配布名。
#   npm：npmのパッケージ名。
#   python-module：Pythonでimportできると確かめたモジュールの最上位の名前。
_NUGET = 'nuget'
_PYPI = 'pypi'
_NPM = 'npm'
_PYTHON_MODULE = 'python-module'


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
    return _state_path_utility.get_state_path('verified-packages', session_id)


def normalize_name(ecosystem, name):
    # NuGetとnpmは大文字小文字を区別しない。PyPIはPEP 503の規則で `-`・`_`・`.` の連続を `-` にそろえる。
    if ecosystem == _PYPI:
        return re.sub(r'[-_.]+', '-', name).lower()
    if ecosystem in (_NUGET, _NPM):
        return name.lower()
    return name


def record_verified(state_path, packages):
    # packages：(エコシステム, 名前) のリスト。
    with _state_path_utility.open_state_database(
        state_path,
        'CREATE TABLE IF NOT EXISTS verified (ecosystem TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (ecosystem, name))',
    ) as connection:
        connection.executemany(
            'INSERT OR IGNORE INTO verified (ecosystem, name) VALUES (?, ?)',
            [(ecosystem, normalize_name(ecosystem, name)) for ecosystem, name in packages],
        )


def get_verified(state_path):
    # 戻り値は (エコシステム, 正規化した名前) の集合。状態ファイルがなければ空。
    return set(_state_path_utility.read_state_database(state_path, 'SELECT ecosystem, name FROM verified'))
