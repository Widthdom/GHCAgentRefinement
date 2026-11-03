import hashlib
import os
import runpy
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'assertion-failure-values-state.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class AssertionFailureValuesStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-assertion-failure-values-state-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.__temp_directory = temporary.name

    def test_state_path(self):
        session_id = uuid.uuid4()
        self.assertEqual(
            _utility['get_state_path'](f' {str(session_id).upper()} '),
            os.path.join(self.__temp_directory, 'ghc-agent-refinement', 'hook-states', 'test-failure-values',
                         f'{session_id}.sqlite3'),
        )
        for invalid in (None, '', '  ', 'not-a-uuid'):
            with self.subTest(session_id=invalid), self.assertRaises(ValueError):
                _utility['get_state_path'](invalid)

    def test_normalize_value(self):
        normalize_value = _utility['normalize_value']
        limit = _utility['_MAX_VALUE_LENGTH']
        for value, expected in (
            ('  42 ', '42'),
            ('"text",', 'text'),
            ("'a'", 'a'),
            ('`b`', 'b'),
            ('"mixed\'', '"mixed\''),
            ('""', None),
            ('   ', None),
            ('a\nb', None),
            ('x' * limit, 'x' * limit),
            ('x' * (limit + 1), None),
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_value(value), expected)

    def test_keys(self):
        get_pair_key = _utility['get_pair_key']
        self.assertEqual(get_pair_key('1', '2'), get_pair_key('1', '2'))
        # 期待値と実際の値の順序を区別する。
        self.assertNotEqual(get_pair_key('1', '2'), get_pair_key('2', '1'))
        self.assertEqual(_utility['get_value_key']('値'), hashlib.sha256('値'.encode('utf-8')).hexdigest())
        path = os.path.join(self.__temp_directory, 'Test.py')
        self.assertEqual(
            _utility['get_file_key'](path), hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest(),
        )

    def test_open_state(self):
        state_path = _utility['get_state_path'](str(uuid.uuid4()))
        with _utility['open_state'](state_path) as connection:
            connection.execute('INSERT INTO pairs (pair) VALUES (?)', ('p',))
            connection.execute('INSERT INTO hardcoding_denials (file) VALUES (?)', ('f',))
        with _utility['open_state'](state_path) as connection:
            tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            pairs = connection.execute('SELECT pair FROM pairs').fetchall()
        self.assertEqual(tables, {'pairs', 'expected_values', 'denials', 'hardcoding_denials'})
        self.assertEqual(pairs, [('p',)])


if __name__ == '__main__':
    run_tests()
