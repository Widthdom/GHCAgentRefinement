import os
import runpy
import sqlite3
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'filter_first_time_denials.py'))
_filter_first_time_denials = _utility['filter_first_time_denials']
_is_first_time_file_denial = _utility['is_first_time_file_denial']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook_test_output.py'))['run_tests']


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

    def test_file_denial_is_first_time_only_once_per_file(self):
        first = os.path.join('project', 'a.txt')
        second = os.path.join('project', 'b.txt')
        self.assertTrue(_is_first_time_file_denial(self.__state_path, first))
        self.assertFalse(_is_first_time_file_denial(self.__state_path, first))
        self.assertTrue(_is_first_time_file_denial(self.__state_path, second))

    def test_file_denial_stores_only_the_digest(self):
        path = os.path.join('project', 'secret-name.txt')
        _is_first_time_file_denial(self.__state_path, path)
        with sqlite3.connect(self.__state_path) as connection:
            rows = connection.execute('SELECT key FROM first_denials').fetchall()
        connection.close()
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0][0], r'^[0-9a-f]{64}$')
        self.assertNotIn('secret-name', rows[0][0])


if __name__ == '__main__':
    _run_tests()
