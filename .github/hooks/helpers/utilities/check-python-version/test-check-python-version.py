import os
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility_path = os.path.join(_script_directory, 'check-python-version.py')

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class CheckPythonVersionTests(unittest.TestCase):
    def __get_exit_code(self, version_info):
        with patch.object(sys, 'version_info', version_info), self.assertRaises(SystemExit) as context:
            runpy.run_path(_utility_path)
        return context.exception.code

    def test_exit_code_by_version(self):
        for version_info, expected in (
            ((3, 14, 0), 0), ((3, 14, 6), 0), ((3, 15, 0), 0), ((4, 0, 0), 0),
            ((3, 13, 9), 1), ((2, 7, 18), 1),
        ):
            with self.subTest(version_info=version_info):
                self.assertEqual(self.__get_exit_code(version_info), expected)

    def test_current_interpreter(self):
        # 実行中のPythonで起動し、何も出力せずに終了コードだけを返すこと。
        result = subprocess.run([sys.executable, _utility_path], capture_output=True, check=False)
        self.assertEqual(result.returncode, 0 if sys.version_info >= (3, 14) else 1)
        self.assertEqual((result.stdout, result.stderr), (b'', b''))


if __name__ == '__main__':
    _run_tests()
