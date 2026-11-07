import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'run-git.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class RunGitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-run-git-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__repository = os.path.join(self.__root, 'repository')
        self.__outside = os.path.join(self.__root, 'outside')
        os.makedirs(os.path.join(self.__repository, 'sub'))
        os.makedirs(self.__outside)
        try:
            subprocess.run(['git', 'init', '-q', self.__repository], check=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as ex:
            self.skipTest(f'git is not available: {ex}')
        # 一時領域の上位にあるリポジトリを、リポジトリ外のディレクトリのルートとして見つけないようにする。
        environment = patch.dict(os.environ, {'GIT_CEILING_DIRECTORIES': self.__root})
        environment.start()
        self.addCleanup(environment.stop)

    def test_run_git_returns_completed_process_with_bytes(self):
        result = _utility['run_git'](self.__repository, ['rev-parse', '--is-inside-work-tree'], 10)
        self.assertEqual((result.returncode, result.stdout), (0, b'true\n'))
        # 失敗も終了コードとして返し、判定は呼び出し元に任せる。
        result = _utility['run_git'](self.__outside, ['rev-parse', '--show-toplevel'], 10)
        self.assertNotEqual(result.returncode, 0)

    def test_get_git_output(self):
        self.assertEqual(_utility['get_git_output'](self.__repository, ['rev-parse', '--is-inside-work-tree'], 10), 'true\n')
        self.assertIsNone(_utility['get_git_output'](self.__outside, ['rev-parse', '--show-toplevel'], 10))

    def test_get_repository_root(self):
        root = _utility['get_repository_root'](os.path.join(self.__repository, 'sub'), 10)
        self.assertEqual(os.path.normcase(os.path.realpath(root)), os.path.normcase(self.__repository))
        self.assertIsNone(_utility['get_repository_root'](self.__outside, 10))

    def test_missing_git_and_timeout_return_none(self):
        for error in (FileNotFoundError('git'), subprocess.TimeoutExpired(['git'], 1)):
            with self.subTest(error=type(error).__name__), patch('subprocess.run', side_effect=error):
                self.assertIsNone(_utility['run_git'](self.__repository, ['status'], 1))
                self.assertIsNone(_utility['get_git_output'](self.__repository, ['status'], 1))
                self.assertIsNone(_utility['get_repository_root'](self.__repository, 1))


if __name__ == '__main__':
    run_tests()
