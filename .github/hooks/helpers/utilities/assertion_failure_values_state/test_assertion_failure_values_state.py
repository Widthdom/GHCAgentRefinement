import hashlib
import os
import sys
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_directory)
import assertion_failure_values_state as _utility

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests
from temporary_state_test_case import TemporaryStateTestCase as _TemporaryStateTestCase


class AssertionFailureValuesStateTests(_TemporaryStateTestCase):
    def test_state_path(self):
        self.assert_state_path(_utility.get_state_path, 'test_failure_values')

    def test_normalize_value(self):
        normalize_value = _utility.normalize_value
        limit = _utility._MAX_VALUE_LENGTH
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
        get_pair_key = _utility.get_pair_key
        self.assertEqual(get_pair_key('1', '2'), get_pair_key('1', '2'))
        # 期待値と実際の値の順序を区別する。
        self.assertNotEqual(get_pair_key('1', '2'), get_pair_key('2', '1'))
        self.assertEqual(_utility.get_value_key('値'), hashlib.sha256('値'.encode('utf-8')).hexdigest())
        path = os.path.join(self.temp_directory, 'Test.py')
        self.assertEqual(
            _utility.get_file_key(path), hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest(),
        )

    def test_open_state(self):
        state_path = _utility.get_state_path(str(uuid.uuid4()))
        with _utility.open_state(state_path) as connection:
            connection.execute('INSERT INTO pairs (pair) VALUES (?)', ('p',))
            connection.execute('INSERT INTO hardcoding_denials (file) VALUES (?)', ('f',))
        with _utility.open_state(state_path) as connection:
            tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            pairs = connection.execute('SELECT pair FROM pairs').fetchall()
        self.assertEqual(tables, {'pairs', 'expected_values', 'denials', 'hardcoding_denials'})
        self.assertEqual(pairs, [('p',)])


if __name__ == '__main__':
    _run_tests()
