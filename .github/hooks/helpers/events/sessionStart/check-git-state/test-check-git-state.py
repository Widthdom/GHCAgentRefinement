import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'check-git-state.py')

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


def _run_hook(input_bytes):
    return subprocess.run([sys.executable, _hook_path], input=input_bytes, capture_output=True)


def _run_hook_in(cwd):
    return _run_hook(json.dumps({'timestamp': 0, 'cwd': cwd, 'source': 'new'}).encode('utf-8'))


def _git(cwd, *args, check=True):
    return subprocess.run(
        ['git', '-c', 'user.name=test', '-c', 'user.email=test@example.com', '-c', 'commit.gpgsign=false', *args],
        cwd=cwd, check=check, capture_output=True,
    )


def _write(path, content):
    with open(path, 'w', encoding='utf-8', newline='\n') as file:
        file.write(content)


class CheckGitStateTests(unittest.TestCase):
    def setUp(self):
        if shutil.which('git') is None:
            self.skipTest('git is not installed.')
        self.__directory = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.__directory, ignore_errors=True)
        self.__repository = os.path.join(self.__directory, 'repository')
        os.makedirs(self.__repository)
        _git(self.__repository, 'init', '--quiet', '--initial-branch=main')
        _write(os.path.join(self.__repository, 'file.txt'), 'base\n')
        _git(self.__repository, 'add', 'file.txt')
        _git(self.__repository, 'commit', '--quiet', '-m', 'base')

    def __git_directory(self):
        return os.path.join(self.__repository, '.git')

    def __assert_silent(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_notified(self, result, expected_parts):
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, output in (('stdout', result.stdout), ('stderr', result.stderr)):
            self.assertFalse(output.startswith(b'\xef\xbb\xbf'), f'{name} must not start with a BOM.')
        context = json.loads(result.stdout.decode('utf-8'))['additionalContext']
        stderr = result.stderr.decode('utf-8')
        for part in expected_parts:
            self.assertIn(part, context)
            self.assertIn(part, stderr)
        self.assertIn('Do not continue, abort, or resolve the operation', context)

    def test_clean_repository(self):
        self.__assert_silent(_run_hook_in(self.__repository), 'A clean repository must not be reported.')

    def test_uncommitted_changes_are_ignored(self):
        _write(os.path.join(self.__repository, 'file.txt'), 'changed\n')
        _write(os.path.join(self.__repository, 'new.txt'), 'new\n')
        self.__assert_silent(_run_hook_in(self.__repository), 'Uncommitted changes must not be reported.')

    def test_outside_repository(self):
        outside = os.path.join(self.__directory, 'outside')
        os.makedirs(outside)
        # 一時フォルダーの上位がリポジトリだと、リポジトリ外を作れない。
        probe = subprocess.run(['git', 'rev-parse', '--git-dir'], cwd=outside, capture_output=True)
        if probe.returncode == 0:
            self.skipTest('The temporary directory is inside a Git repository.')
        self.__assert_silent(_run_hook_in(outside), 'A directory outside a repository must not be reported.')

    def test_detached_head(self):
        _git(self.__repository, 'checkout', '--quiet', '--detach')
        self.__assert_notified(_run_hook_in(self.__repository), ['HEAD is detached at '])

    def test_merge_conflict(self):
        _git(self.__repository, 'checkout', '--quiet', '-b', 'other')
        _write(os.path.join(self.__repository, 'file.txt'), 'other\n')
        _git(self.__repository, 'commit', '--quiet', '-am', 'other')
        _git(self.__repository, 'checkout', '--quiet', 'main')
        _write(os.path.join(self.__repository, 'file.txt'), 'main\n')
        _git(self.__repository, 'commit', '--quiet', '-am', 'main')
        merge = _git(self.__repository, 'merge', 'other', check=False)
        self.assertNotEqual(merge.returncode, 0, 'The merge must conflict.')
        self.__assert_notified(
            _run_hook_in(self.__repository),
            ['A merge is in progress.', '1 file(s) have unresolved conflicts: file.txt.'],
        )

    def test_operation_markers(self):
        cases = [
            ('MERGE_HEAD', False, 'A merge is in progress.'),
            ('rebase-merge', True, 'A rebase is in progress.'),
            ('rebase-apply', True, 'A rebase is in progress.'),
            ('CHERRY_PICK_HEAD', False, 'A cherry-pick is in progress.'),
            ('REVERT_HEAD', False, 'A revert is in progress.'),
            ('BISECT_LOG', False, 'A bisect is in progress.'),
        ]
        for marker, is_directory, expected in cases:
            with self.subTest(marker=marker):
                path = os.path.join(self.__git_directory(), marker)
                if is_directory:
                    os.makedirs(path)
                else:
                    _write(path, '0000000000000000000000000000000000000000\n')
                try:
                    self.__assert_notified(_run_hook_in(self.__repository), [expected])
                finally:
                    # 次のケースに目印を残さない。
                    if is_directory:
                        shutil.rmtree(path)
                    else:
                        os.remove(path)

    def test_git_am(self):
        rebase_apply = os.path.join(self.__git_directory(), 'rebase-apply')
        os.makedirs(rebase_apply)
        _write(os.path.join(rebase_apply, 'applying'), '')
        self.__assert_notified(_run_hook_in(self.__repository), ['A git am is in progress.'])

    def test_detached_head_during_operation_is_not_reported_separately(self):
        _git(self.__repository, 'checkout', '--quiet', '--detach')
        os.makedirs(os.path.join(self.__git_directory(), 'rebase-merge'))
        result = _run_hook_in(self.__repository)
        self.__assert_notified(result, ['A rebase is in progress.'])
        self.assertNotIn(b'HEAD is detached', result.stdout)

    def test_invalid_input(self):
        relative = json.dumps({'cwd': 'relative'}).encode('utf-8')
        missing = json.dumps({'cwd': os.path.join(self.__directory, 'missing')}).encode('utf-8')
        for name, input_bytes in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('no cwd', b'{}'),
            ('relative cwd', relative),
            ('missing cwd', missing),
            ('invalid utf-8', b'\xff'),
        ]:
            with self.subTest(name=name):
                result = _run_hook(input_bytes)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check the Git state:', result.stderr)


if __name__ == '__main__':
    run_tests()
