import hashlib
import os
import sqlite3
import sys
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_directory)
import known_files_state as _utility

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests
from temporary_state_test_case import TemporaryStateTestCase as _TemporaryStateTestCase


class KnownFilesStateTests(_TemporaryStateTestCase):
    def test_state_path(self):
        self.assert_state_path(_utility.get_state_path, 'known_files')

    def test_normalize_path_and_key(self):
        normalize_path = _utility.normalize_path
        # normalize_pathは実体のパスを返すため、Windowsの短縮名（RUNNER~1など）を解決した一時フォルダーを基準にする。
        cwd = os.path.join(os.path.realpath(self.temp_directory), 'repository')
        expected = os.path.normcase(os.path.join(cwd, 'src', 'a.py'))
        # 相対パス・`..` を含むパス・絶対パスを同じファイルとみなす。
        for path in ('src/a.py', os.path.join('src', '..', 'src', 'a.py'), os.path.join(cwd, 'src', 'a.py')):
            with self.subTest(path=path):
                self.assertEqual(normalize_path(cwd, path), expected)
        self.assertEqual(_utility.get_file_key(expected), hashlib.sha256(expected.encode('utf-8')).hexdigest())

    def test_open_state(self):
        state_path = _utility.get_state_path(str(uuid.uuid4()))
        with _utility.open_state(state_path) as connection:
            connection.execute('INSERT INTO files (file, state) VALUES (?, ?)', ('k1', _utility.SEEN))
            connection.execute('INSERT INTO files (file, state) VALUES (?, ?)', ('k2', _utility.DENIED))
        # 状態はseen・deniedのどちらかに限る。
        with self.assertRaises(sqlite3.IntegrityError), _utility.open_state(state_path) as connection:
            connection.execute('INSERT INTO files (file, state) VALUES (?, ?)', ('k3', 'unknown'))
        with _utility.open_state(state_path) as connection:
            rows = connection.execute('SELECT file, state FROM files ORDER BY file').fetchall()
        self.assertEqual(rows, [('k1', 'seen'), ('k2', 'denied')])


if __name__ == '__main__':
    _run_tests()
