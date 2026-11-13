import os
import runpy
import tempfile
import unittest
import uuid
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_get_debug_log_directory = runpy.run_path(
    os.path.join(_script_directory, 'get-debug-log-directory.py')
)['get_debug_log_directory']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class GetDebugLogDirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-get-debug-log-directory-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.__logs_directory = os.path.join(temporary.name, 'ghc-agent-refinement', 'debug-logs')

    def test_returns_session_directory_without_creating_it(self):
        # 前後の空白と大文字小文字が違っても、同じセッションは同じ保存先になる。
        session_id = uuid.uuid4()
        expected = os.path.join(self.__logs_directory, 'prompt-transformations', str(session_id))
        for spelling in (str(session_id), f' {str(session_id).upper()} '):
            with self.subTest(spelling=spelling):
                self.assertEqual(_get_debug_log_directory('prompt-transformations', spelling), expected)
        self.assertFalse(os.path.exists(self.__logs_directory))

    def test_rejects_invalid_session_id(self):
        # パスの区切りを含む値で、ログの保存先の外を指させない。
        for session_id in (None, '', 'not-a-uuid', '../escape'):
            with self.subTest(session_id=session_id):
                with self.assertRaises(ValueError):
                    _get_debug_log_directory('prompt-transformations', session_id)


if __name__ == '__main__':
    _run_tests()
