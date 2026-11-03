import hashlib
import os
import runpy
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'known-files-state.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class KnownFilesStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-known-files-state-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.temp_directory = temporary.name

    def test_tool_args(self):
        get_tool_args = _utility['get_tool_args']
        self.assertEqual(get_tool_args({'path': 'a'}), {'path': 'a'})
        self.assertEqual(get_tool_args('{"path":"a"}'), {'path': 'a'})
        # 生のパッチ文字列はそのまま返す。
        self.assertEqual(get_tool_args('*** Begin Patch'), '*** Begin Patch')
        self.assertIsNone(get_tool_args(None))

    def test_collect_strings(self):
        value = {'a': 'x', 'b': ['y', {'c': 'z', 'd': 1}], 'e': None}
        self.assertEqual(list(_utility['collect_strings'](value)), ['x', 'y', 'z'])

    def test_state_path(self):
        session_id = uuid.uuid4()
        self.assertEqual(
            _utility['get_state_path'](f' {str(session_id).upper()} '),
            os.path.join(self.temp_directory, 'ghc-agent-refinement', 'hook-states', 'known-files',
                         f'{session_id}.sqlite3'),
        )
        for invalid in (None, '', '  ', 'not-a-uuid'):
            with self.subTest(session_id=invalid), self.assertRaises(ValueError):
                _utility['get_state_path'](invalid)

    def test_normalize_path_and_key(self):
        normalize_path = _utility['normalize_path']
        # normalize_pathは実体のパスを返すため、Windowsの短縮名（RUNNER~1など）を解決した一時フォルダーを基準にする。
        cwd = os.path.join(os.path.realpath(self.temp_directory), 'repository')
        expected = os.path.normcase(os.path.join(cwd, 'src', 'a.py'))
        # 相対パス・`..` を含むパス・絶対パスを同じファイルとみなす。
        for path in ('src/a.py', os.path.join('src', '..', 'src', 'a.py'), os.path.join(cwd, 'src', 'a.py')):
            with self.subTest(path=path):
                self.assertEqual(normalize_path(cwd, path), expected)
        self.assertEqual(_utility['get_file_key'](expected), hashlib.sha256(expected.encode('utf-8')).hexdigest())

    def test_open_state(self):
        state_path = _utility['get_state_path'](str(uuid.uuid4()))
        with _utility['open_state'](state_path) as connection:
            connection.execute('INSERT INTO files (file, state) VALUES (?, ?)', ('k1', _utility['SEEN']))
            connection.execute('INSERT INTO files (file, state) VALUES (?, ?)', ('k2', _utility['DENIED']))
        # 状態はseen・deniedのどちらかに限る。
        with self.assertRaises(sqlite3.IntegrityError), _utility['open_state'](state_path) as connection:
            connection.execute('INSERT INTO files (file, state) VALUES (?, ?)', ('k3', 'unknown'))
        # 例外で抜けた更新は保存しない。
        with self.assertRaises(RuntimeError), _utility['open_state'](state_path) as connection:
            connection.execute('INSERT INTO files (file, state) VALUES (?, ?)', ('k4', _utility['SEEN']))
            raise RuntimeError
        with _utility['open_state'](state_path) as connection:
            rows = connection.execute('SELECT file, state FROM files ORDER BY file').fetchall()
        self.assertEqual(rows, [('k1', 'seen'), ('k2', 'denied')])


if __name__ == '__main__':
    run_tests()
