import os
import runpy
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_get_display_path = runpy.run_path(os.path.join(_script_directory, 'get-display-path.py'))['get_display_path']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class GetDisplayPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-get-display-path-test-')
        self.addCleanup(temporary.cleanup)
        self.__cwd = os.path.join(temporary.name, 'repo')

    def test_paths_under_cwd_are_relative(self):
        for parts, expected in (
            (('a.py',), 'a.py'),
            (('src', 'sub', 'a.py'), 'src/sub/a.py'),
            ((), '.'),
        ):
            with self.subTest(parts=parts):
                self.assertEqual(_get_display_path(os.path.join(self.__cwd, *parts), self.__cwd), expected)

    def test_paths_outside_cwd_are_absolute(self):
        for path in (
            os.path.join(os.path.dirname(self.__cwd), 'other', 'a.py'),
            # 名前の先頭が同じだけの兄弟ディレクトリは配下としない。
            self.__cwd + '-other' + os.sep + 'a.py',
        ):
            with self.subTest(path=path):
                self.assertEqual(_get_display_path(path, self.__cwd), path.replace(os.sep, '/'))

    @unittest.skipUnless(os.name == 'nt', 'Windows only: paths are case-insensitive and may be on different drives.')
    def test_windows_paths(self):
        self.assertEqual(_get_display_path(r'C:\Repo\Src\a.py', r'c:\repo'), 'Src/a.py')
        self.assertEqual(_get_display_path(r'D:\other\a.py', r'C:\repo'), 'D:/other/a.py')


if __name__ == '__main__':
    run_tests()
