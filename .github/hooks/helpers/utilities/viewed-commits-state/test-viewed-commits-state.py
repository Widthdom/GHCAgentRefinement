import os
import runpy
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'viewed-commits-state.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_COMMIT_A = 'a' * 40
_COMMIT_B = 'B' * 40


class ViewedCommitsStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-viewed-commits-state-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.__temp_directory = temporary.name
        self.__state_path = _utility['get_state_path'](str(uuid.uuid4()))

    def test_state_path(self):
        session_id = uuid.uuid4()
        self.assertEqual(
            _utility['get_state_path'](f' {str(session_id).upper()} '),
            os.path.join(self.__temp_directory, 'ghc-agent-refinement', 'hook-states', 'viewed-commits',
                         f'{session_id}.sqlite3'),
        )
        for invalid in (None, '', '  ', 'not-a-uuid'):
            with self.subTest(session_id=invalid), self.assertRaises(ValueError):
                _utility['get_state_path'](invalid)

    def test_record_and_get(self):
        self.assertEqual(_utility['get_viewed'](self.__state_path), set())
        _utility['record_viewed'](self.__state_path, [_COMMIT_A, _COMMIT_B])
        _utility['record_viewed'](self.__state_path, [_COMMIT_A])
        self.assertEqual(_utility['get_viewed'](self.__state_path), {_COMMIT_A, _COMMIT_B.lower()})

    def test_get_without_table(self):
        os.makedirs(os.path.dirname(self.__state_path))
        with closing(sqlite3.connect(self.__state_path)):
            pass
        self.assertEqual(_utility['get_viewed'](self.__state_path), set())


if __name__ == '__main__':
    _run_tests()
