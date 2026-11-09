import os
import runpy
import sqlite3
import tempfile
import unittest
from contextlib import closing

_script_directory = os.path.dirname(os.path.abspath(__file__))
_filter_new_notifications = runpy.run_path(os.path.join(_script_directory, 'filter-new-notifications.py'))['filter_new_notifications']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class FilterNewNotificationsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-filter-new-notifications-test-')
        self.addCleanup(temporary.cleanup)
        # 親ディレクトリがなくても作る。
        self.__state_path = os.path.join(temporary.name, 'hook-states', 'hook', 'session.sqlite3')

    def __filter(self, results):
        return _filter_new_notifications(self.__state_path, results)

    def test_returns_values_with_new_problems(self):
        self.assertEqual(self.__filter([('a', 'value a', ['x']), ('b', 'value b', ['y'])]), ['value a', 'value b'])
        # 通知済みの問題だけなら返さず、新しい問題が加われば通知済みの問題を含む組を返す。
        self.assertEqual(self.__filter([('a', 'again a', ['x']), ('b', 'again b', ['y', 'z'])]), ['again b'])
        self.assertEqual(self.__filter([('a', 'again a', ['x']), ('b', 'again b', ['y', 'z'])]), [])

    def test_resolved_problems_are_notified_again(self):
        self.assertEqual(self.__filter([('a', 1, ['x'])]), [1])
        self.assertEqual(self.__filter([('a', 2, [])]), [])
        self.assertEqual(self.__filter([('a', 3, ['x'])]), [3])

    def test_keys_are_independent(self):
        self.assertEqual(self.__filter([('a', 1, ['x'])]), [1])
        self.assertEqual(self.__filter([('b', 2, ['x'])]), [2])

    def test_records_persist_across_connections(self):
        self.__filter([('a', None, ['x', 'y']), ('b', None, [])])
        with closing(sqlite3.connect(self.__state_path)) as connection:
            rows = connection.execute('SELECT key, problem FROM notified_problems ORDER BY problem').fetchall()
        self.assertEqual(rows, [('a', 'x'), ('a', 'y')])

    def test_invalid_state_file(self):
        os.makedirs(os.path.dirname(self.__state_path))
        with open(self.__state_path, 'wb') as file:
            file.write(b'not a database')
        with self.assertRaises(sqlite3.Error):
            self.__filter([('a', None, ['x'])])


if __name__ == '__main__':
    _run_tests()
