import os
import runpy
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'get-state-path.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class GetStatePathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-get-state-path-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.__states_directory = os.path.join(temporary.name, 'ghc-agent-refinement', 'hook-states')

    def test_normalizes_session_id(self):
        # 前後の空白と大文字小文字が違っても、同じセッションは同じ保存先になる。
        session_id = uuid.uuid4()
        self.assertEqual(_utility['parse_session_id'](f' {str(session_id).upper()} '), session_id)
        self.assertEqual(
            _utility['get_state_path']('known-files', f' {str(session_id).upper()} '),
            os.path.join(self.__states_directory, 'known-files', f'{session_id}.sqlite3'),
        )

    def test_extension(self):
        session_id = str(uuid.uuid4())
        self.assertEqual(
            _utility['get_state_path']('debug-mode', session_id, '.json'),
            os.path.join(self.__states_directory, 'debug-mode', f'{session_id}.json'),
        )

    def test_rejects_invalid_session_id(self):
        for session_id, message in (
            (None, 'sessionId must be a non-empty UUID string.'),
            ('', 'sessionId must be a non-empty UUID string.'),
            (' ', 'sessionId must be a non-empty UUID string.'),
            (uuid.uuid4(), 'sessionId must be a non-empty UUID string.'),
            ('not-a-uuid', 'sessionId must be a valid UUID.'),
            # パスの区切りを含む値で、状態の保存先の外を指させない。
            ('../escape', 'sessionId must be a valid UUID.'),
        ):
            with self.subTest(session_id=session_id):
                for call in (
                    lambda: _utility['parse_session_id'](session_id),
                    lambda: _utility['get_state_path']('known-files', session_id),
                ):
                    with self.assertRaises(ValueError) as context:
                        call()
                    self.assertEqual(str(context.exception), message)


if __name__ == '__main__':
    run_tests()
