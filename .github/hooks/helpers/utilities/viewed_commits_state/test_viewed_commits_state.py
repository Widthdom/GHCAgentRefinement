import os
import sys
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_directory)
import viewed_commits_state as _utility

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests
from temporary_state_test_case import TemporaryStateTestCase as _TemporaryStateTestCase

_COMMIT_A = 'a' * 40
_COMMIT_B = 'B' * 40


class ViewedCommitsStateTests(_TemporaryStateTestCase):
    def setUp(self):
        super().setUp()
        self.__state_path = _utility.get_state_path(str(uuid.uuid4()))

    def test_state_path(self):
        self.assert_state_path(_utility.get_state_path, 'viewed_commits')

    def test_record_and_get(self):
        self.assertEqual(_utility.get_viewed(self.__state_path), set())
        _utility.record_viewed(self.__state_path, [_COMMIT_A, _COMMIT_B])
        _utility.record_viewed(self.__state_path, [_COMMIT_A])
        self.assertEqual(_utility.get_viewed(self.__state_path), {_COMMIT_A, _COMMIT_B.lower()})


if __name__ == '__main__':
    _run_tests()
