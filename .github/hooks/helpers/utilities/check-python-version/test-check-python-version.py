import os
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

script_directory = os.path.dirname(os.path.abspath(__file__))
utility_path = os.path.join(script_directory, 'check-python-version.py')

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class CheckPythonVersionTests(unittest.TestCase):
    def get_exit_code(self, version_info):
        with patch.object(sys, 'version_info', version_info), self.assertRaises(SystemExit) as context:
            runpy.run_path(utility_path)
        return context.exception.code

    def test_exit_code_by_version(self):
        for version_info, expected in (
            ((3, 14, 0), 0), ((3, 14, 6), 0), ((3, 15, 0), 0), ((4, 0, 0), 0),
            ((3, 13, 9), 1), ((2, 7, 18), 1),
        ):
            with self.subTest(version_info=version_info):
                self.assertEqual(self.get_exit_code(version_info), expected)

    def test_current_interpreter(self):
        # 実行中のPythonで起動し、何も出力せずに終了コードだけを返すこと。
        result = subprocess.run([sys.executable, utility_path], capture_output=True, check=False)
        self.assertEqual(result.returncode, 0 if sys.version_info >= (3, 14) else 1)
        self.assertEqual((result.stdout, result.stderr), (b'', b''))


if __name__ == '__main__':
    run_tests()
