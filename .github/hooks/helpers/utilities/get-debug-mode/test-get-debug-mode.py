import os
import runpy
import tempfile
import unittest
import uuid
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_get_debug_mode = runpy.run_path(os.path.join(_script_directory, 'get-debug-mode.py'))['get_debug_mode']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class GetDebugModeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-get-debug-mode-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.__state_directory = os.path.join(temporary.name, 'ghc-agent-refinement', 'hook-states', 'debug-mode')
        self.__session_id = str(uuid.uuid4())

    def __write_state(self, content, session_id=None, encoding='utf-8'):
        os.makedirs(self.__state_directory, exist_ok=True)
        path = os.path.join(self.__state_directory, f'{session_id or self.__session_id}.json')
        with open(path, 'w', encoding=encoding, newline='') as file:
            file.write(content)

    def test_missing_state_is_disabled(self):
        self.assertIs(_get_debug_mode(self.__session_id), False)

    def test_only_true_enables(self):
        for content, expected in (
            ('{"enabled":true}', True),
            ('{"enabled":false}', False),
            ('{"enabled":"true"}', False),
            ('{"enabled":1}', False),
            ('{}', False),
        ):
            with self.subTest(content=content):
                self.__write_state(content)
                self.assertIs(_get_debug_mode(self.__session_id), expected)

    def test_reads_bom_and_separates_sessions(self):
        self.__write_state('{"enabled":true}', encoding='utf-8-sig')
        self.assertIs(_get_debug_mode(self.__session_id), True)
        self.assertIs(_get_debug_mode(str(uuid.uuid4())), False)

    def test_rejects_invalid_state(self):
        for content, error in (('[]', ValueError), ('{', ValueError)):
            with self.subTest(content=content):
                self.__write_state(content)
                with self.assertRaises(error):
                    _get_debug_mode(self.__session_id)


if __name__ == '__main__':
    _run_tests()
