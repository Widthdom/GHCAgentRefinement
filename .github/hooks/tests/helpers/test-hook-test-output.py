import contextlib
import io
import os
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_test_output import HookTestRunner, run_tests


_tests_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class HookTestOutputTests(unittest.TestCase):
    def test_live_progress(self):
        stream = io.StringIO()

        def first():
            self.assertEqual(stream.getvalue(), '')

        def second():
            self.assertEqual(stream.getvalue(), '.')

        suite = unittest.TestSuite([
            unittest.FunctionTestCase(first),
            unittest.FunctionTestCase(second),
        ])
        result = HookTestRunner(stream=stream).run(suite)
        self.assertTrue(result.wasSuccessful())
        self.assertTrue(stream.getvalue().startswith('..\n'))
        self.assertTrue(stream.getvalue().endswith('OK\n'))

    def __invoke(self, body, *arguments):
        source = (
            'import sys\n'
            f'sys.path.insert(0, {os.path.join(_tests_directory, "helpers")!r})\n'
            'import unittest\n'
            'from hook_test_output import run_tests\n'
            f'sys.argv = ["test-example.py", *{list(arguments)!r}]\n'
            'class ExampleTests(unittest.TestCase):\n'
            f'{body}\n'
            'run_tests()\n'
        )
        return subprocess.run(
            [sys.executable, '-c', source],
            capture_output=True, text=True, check=False,
        )

    def test_success(self):
        result = self.__invoke(
            '    def test_first(self): pass\n'
            '    def test_second(self): pass'
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertRegex(result.stderr, r'\A\.\.\n-+\nRan 2 tests in \d+\.\d{3}s\n\nOK\n\Z')

    def test_failure(self):
        result = self.__invoke('    def test_failure(self): self.fail("failure detail")')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('FAIL: test_failure', result.stderr)
        self.assertIn('AssertionError: failure detail', result.stderr)
        self.assertIn('FAILED (failures=1)', result.stderr)
        self.assertTrue(result.stderr.endswith('NG\n'))

    def test_error(self):
        result = self.__invoke('    def test_error(self): raise ValueError("error detail")')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('ERROR: test_error', result.stderr)
        self.assertIn('ValueError: error detail', result.stderr)
        self.assertIn('FAILED (errors=1)', result.stderr)
        self.assertTrue(result.stderr.endswith('NG\n'))

    def test_skip(self):
        result = self.__invoke(
            '    @unittest.skip("skip reason")\n'
            '    def test_skip(self): pass\n'
            '    def test_success(self): pass'
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertTrue(result.stderr.startswith('s.\n'))
        self.assertIn('Ran 2 tests in ', result.stderr)
        self.assertTrue(result.stderr.endswith('OK (skipped=1)\n'))
        self.assertNotIn('NG', result.stderr)

    def test_verbose_skip(self):
        result = self.__invoke(
            '    @unittest.skip("skip reason")\n'
            '    def test_skip(self): pass',
            '-v',
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("skipped 'skip reason'", result.stderr)
        self.assertTrue(result.stderr.endswith('OK (skipped=1)\n'))

    def test_unexpected_success(self):
        result = self.__invoke(
            '    @unittest.expectedFailure\n'
            '    def test_unexpected_success(self): pass'
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('UNEXPECTED SUCCESS', result.stderr)
        self.assertTrue(result.stderr.endswith('NG\n'))

    def test_subtest_failure(self):
        result = self.__invoke(
            '    def test_subtest(self):\n'
            '        with self.subTest(value=1): self.fail("subtest detail")'
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('subtest detail', result.stderr)
        self.assertIn('value=1', result.stderr)
        self.assertTrue(result.stderr.endswith('NG\n'))

    def test_selection(self):
        result = self.__invoke(
            '    def test_first(self): pass\n'
            '    def test_second(self): self.fail("not selected")',
            'ExampleTests.test_first',
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('Ran 1 test in ', result.stderr)
        self.assertTrue(result.stderr.endswith('OK\n'))

    def test_failfast(self):
        result = self.__invoke(
            '    def test_first(self): self.fail("first failure")\n'
            '    def test_second(self): self.fail("second failure")',
            '-f',
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('Ran 1 test in ', result.stderr)
        self.assertNotIn('second failure', result.stderr)
        self.assertTrue(result.stderr.endswith('NG\n'))

    def test_expected_failure(self):
        result = self.__invoke(
            '    @unittest.expectedFailure\n'
            '    def test_expected_failure(self): self.fail("expected")',
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stderr.startswith('x\n'))
        self.assertTrue(result.stderr.endswith('OK (expected failures=1)\n'))


class AllHooksOutputTests(unittest.TestCase):
    def __invoke(self, results, filenames=('test-first.py', 'test-second.py')):
        stdout = io.StringIO()
        stderr = io.StringIO()
        helpers_directory = os.path.join(os.path.dirname(_tests_directory), 'helpers')
        exit_code = 0
        with (
            patch('os.walk', return_value=[(helpers_directory, [], filenames)]),
            patch('subprocess.run', side_effect=results) as run,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                runpy.run_path(os.path.join(_tests_directory, 'test-all-hooks.py'))
            except SystemExit as ex:
                exit_code = ex.code
        return exit_code, stdout.getvalue(), stderr.getvalue(), run.call_args_list

    def test_success(self):
        result = self.__invoke([
            subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
        ])
        self.assertEqual(result[0], 0)
        self.assertTrue(result[1].endswith('Passed all 2 hook test files.\nOK\n'))
        self.assertEqual(result[2], '')
        self.assertEqual(len(result[3]), 2)
        # テストはリポジトリルートで実行し、パスはリポジトリルート相対の `/` 区切りで表示する。
        self.assertEqual(result[3][0].kwargs['cwd'], os.path.dirname(os.path.dirname(os.path.dirname(_tests_directory))))
        self.assertIn('Running .github/hooks/helpers/test-first.py...', result[1])

    def test_failure_continues_other_files(self):
        result = self.__invoke([
            subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0),
        ])
        self.assertEqual(result[0], 2)
        self.assertNotIn('OK', result[1])
        self.assertIn('exited with code 1', result[2])
        self.assertTrue(result[2].endswith('NG\n'))
        self.assertEqual(len(result[3]), 2)

    def test_start_failure(self):
        result = self.__invoke([OSError('start detail'), subprocess.CompletedProcess([], 0)])
        self.assertEqual(result[0], 2)
        self.assertIn('start detail', result[2])
        self.assertIn('could not start', result[2])
        self.assertTrue(result[2].endswith('NG\n'))
        self.assertEqual(len(result[3]), 2)

    def test_no_test_files(self):
        result = self.__invoke([], filenames=())
        self.assertEqual(result[0], 2)
        self.assertEqual(result[1], '')
        self.assertIn('No hook tests found', result[2])
        self.assertTrue(result[2].endswith('NG\n'))
        self.assertEqual(len(result[3]), 0)


if __name__ == '__main__':
    run_tests()
