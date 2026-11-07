import os
import runpy
import sqlite3
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_filter_first_time_denials = runpy.run_path(os.path.join(_script_directory, 'filter-first-time-denials.py'))['filter_first_time_denials']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class FilterFirstTimeDenialsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-filter-first-time-denials-test-')
        self.addCleanup(temporary.cleanup)
        # 親ディレクトリがなくても作る。
        self.__state_path = os.path.join(temporary.name, 'hook-states', 'hook', 'session.sqlite3')

    def test_returns_only_first_time_denials(self):
        self.assertEqual(_filter_first_time_denials(self.__state_path, [('a', 'reason a'), ('b', 'reason b')]),
                         ['reason a', 'reason b'])
        self.assertEqual(_filter_first_time_denials(self.__state_path, [('b', 'again b'), ('c', 'reason c')]), ['reason c'])
        self.assertEqual(_filter_first_time_denials(self.__state_path, [('a', 'again a'), ('c', 'again c')]), [])

    def test_duplicate_keys_return_only_the_first_value(self):
        self.assertEqual(_filter_first_time_denials(self.__state_path, [('a', 1), ('a', 2), ('b', 3)]), [1, 3])

    def test_empty_candidates(self):
        self.assertEqual(_filter_first_time_denials(self.__state_path, []), [])
        self.assertTrue(os.path.isfile(self.__state_path))

    def test_records_persist_across_connections(self):
        _filter_first_time_denials(self.__state_path, [('a', None)])
        with sqlite3.connect(self.__state_path) as connection:
            rows = connection.execute('SELECT key FROM first_denials').fetchall()
        connection.close()
        self.assertEqual(rows, [('a',)])

    def test_invalid_state_file(self):
        os.makedirs(os.path.dirname(self.__state_path))
        with open(self.__state_path, 'wb') as file:
            file.write(b'not a database')
        with self.assertRaises(sqlite3.Error):
            _filter_first_time_denials(self.__state_path, [('a', None)])


if __name__ == '__main__':
    run_tests()
