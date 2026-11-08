import json
import os
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-vacuous-test-success.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_get_hook_entry = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-entry.py'))['get_hook_entry']


class DetectVacuousTestSuccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-vacuous-test-success-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__root, 'ghc-agent-refinement', 'hook-states', 'test-counts', f'{self.__session_id}.sqlite3',
        )

    def __payload(self, command, output, tool_name='bash', session_id=None):
        return {
            'sessionId': session_id or self.__session_id,
            'timestamp': 0,
            'cwd': self.__root,
            'toolName': tool_name,
            'toolArgs': {'command': command},
            'toolResult': {'resultType': 'success', 'textResultForLlm': output},
        }

    def __invoke(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __run_test(self, command, output, **kwargs):
        return self.__invoke(self.__payload(command, output, **kwargs))

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_context(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'additionalContext'})
        for fragment in fragments:
            self.assertIn(fragment, output['additionalContext'])
        return output['additionalContext']

    def __read_rows(self):
        with closing(sqlite3.connect(self.__state_path)) as connection:
            return connection.execute('SELECT command, executed, skipped FROM counts').fetchall()

    def test_configuration(self):
        entry = _get_hook_entry(_hook_path)
        self.assertEqual(entry['matcher'], '(?:functions\\.)?(?:bash|powershell)')
        self.assertEqual(entry['timeoutSec'], 10)

    def test_notifies_zero_tests_from_counts(self):
        cases = (
            ('pytest -k missing', '=== 5 deselected in 0.01s ===', '0 tests executed, 5 skipped'),
            ('python -m unittest', '----\nRan 0 tests in 0.000s\n\nOK', '0 tests executed'),
            ('npx jest -t nothing', 'Tests:       3 skipped, 3 total', '0 tests executed, 3 skipped'),
            ('npx vitest run', ' Tests  2 skipped (2)', '0 tests executed, 2 skipped'),
            ('npx mocha', '  0 passing (1ms)', '0 tests executed'),
            ('bundle exec rspec', '0 examples, 0 failures', '0 tests executed'),
            ('cargo test foo', 'test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 9 filtered out', '0 tests executed'),
            ('dotnet test', 'Passed!  - Failed:     0, Passed:     0, Skipped:     0, Total:     0', '0 tests executed'),
            ('Invoke-Pester', 'Tests Passed: 0, Failed: 0, Skipped: 0 NotRun: 0', '0 tests executed'),
            ('go test -run Nope ./...', 'ok  \texample.com/a\t0.01s [no tests to run]', '0 tests executed'),
        )
        for command, output, detail in cases:
            with self.subTest(command=command):
                self.__assert_context(self.__run_test(command, output), f'`{command}` ran no tests ({detail})')

    def test_notifies_zero_tests_from_messages(self):
        cases = (
            ('pytest tests/missing.py', 'collected 0 items\n\nno tests ran in 0.01s', 'no tests ran'),
            ('npx jest x', 'No tests found, exiting with code 0', 'No tests found'),
            ('npx vitest run x', 'No test files found, exiting with code 1', 'No test files found'),
            ('dotnet test --filter X', 'No test matches the given testcase filter `X` in /a.dll', 'No test matches the given testcase filter'),
            ('go test -run X', 'testing: warning: no tests to run\nPASS', 'testing: warning: no tests to run'),
            ('vendor/bin/phpunit --filter X', 'No tests executed!', 'No tests executed'),
        )
        for command, output, phrase in cases:
            with self.subTest(command=command):
                self.__assert_context(self.__run_test(command, output), f'(output: "{phrase}")')

    def test_strips_ansi_escape_sequences(self):
        output = '\x1b[32m\x1b[1m=== 5 deselected in 0.01s ===\x1b[0m'
        self.__assert_context(self.__run_test('pytest -k x', output), 'ran no tests')

    def test_does_not_repeat_zero_notification_for_same_command(self):
        self.__assert_context(self.__run_test('pytest -k x', 'no tests ran in 0.01s'))
        self.__assert_silent(self.__run_test('pytest -k x', 'no tests ran in 0.01s'))
        self.__assert_context(self.__run_test('pytest -k y', 'no tests ran in 0.01s'))

    def test_notifies_decrease_for_same_command(self):
        self.__assert_silent(self.__run_test('pytest', '=== 10 passed, 1 skipped in 0.5s ==='))
        self.__assert_context(
            self.__run_test('pytest', '=== 8 passed, 3 skipped in 0.4s ==='),
            '`pytest` ran 8 tests instead of 10 and skipped 3 tests instead of 1',
            'restore them',
        )
        self.__assert_silent(self.__run_test('pytest', '=== 8 passed, 3 skipped in 0.4s ==='))
        self.__assert_silent(self.__run_test('pytest', '=== 9 passed, 2 skipped in 0.4s ==='))
        self.__assert_context(self.__run_test('pytest', '=== 9 passed, 4 skipped in 0.4s ==='), 'skipped 4 tests instead of 2')

    def test_failed_tests_count_as_executed(self):
        self.__assert_silent(self.__run_test('npm test', 'Tests:       2 failed, 3 passed, 5 total'))
        self.__assert_silent(self.__run_test('npm test', 'Tests:       5 passed, 5 total'))
        self.__assert_context(self.__run_test('npm test', 'Tests:       1 failed, 3 passed, 4 total'), 'ran 4 tests instead of 5')

    def test_sums_multiple_summaries(self):
        output = '\n'.join(['Ran 3 tests in 0.1s', 'OK', 'Ran 4 tests in 0.1s', 'OK (skipped=1)'])
        self.__assert_silent(self.__run_test('python .github/hooks/tests/test-all-hooks.py', output))
        self.assertEqual([row[1:] for row in self.__read_rows()], [(6, 1)])
        output = '\n'.join([
            'test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured',
            'test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured',
        ])
        self.__assert_silent(self.__run_test('cargo test', output))
        go = 'ok  \texample.com/a\t0.01s\n?   \texample.com/b\t[no test files]\nok  \texample.com/c\t(cached)'
        self.__assert_silent(self.__run_test('go test ./...', go))
        self.assertEqual(sorted(row[1:] for row in self.__read_rows()), [(2, 0), (3, 0), (6, 1)])

    def test_other_formats_are_counted(self):
        cases = (
            ('npx mocha', '  3 passing (12ms)\n  1 failing\n  2 pending', (4, 2)),
            ('rspec', '5 examples, 1 failure, 2 pending', (3, 2)),
            ('dotnet test', 'Failed!  - Failed:     1, Passed:     5, Skipped:     1, Total:     7', (6, 1)),
            ('phpunit', 'OK (3 tests, 5 assertions)', (3, 0)),
            ('phpunit tests', 'Tests: 4, Assertions: 6, Failures: 1, Skipped: 1.', (3, 1)),
            ('Invoke-Pester x', 'Tests Passed: 3, Failed: 1, Skipped: 2 NotRun: 0', (4, 2)),
            ('npx vitest', ' Tests  3 passed | 1 skipped (4)', (3, 1)),
        )
        for command, output, expected in cases:
            with self.subTest(command=command):
                self.__assert_silent(self.__run_test(command, output, tool_name='functions.powershell'))
                with closing(sqlite3.connect(self.__state_path)) as connection:
                    count = connection.execute('SELECT COUNT(*) FROM counts WHERE executed = ? AND skipped = ?', expected).fetchone()[0]
                self.assertGreaterEqual(count, 1)

    def test_skips_unrelated_calls_without_state(self):
        payloads = (
            self.__payload('git status', 'Ran 0 tests in 0.0s'),
            self.__payload('pytest', 'some output without summary'),
            self.__payload('pytest', ''),
            {**self.__payload('pytest', 'x'), 'toolResult': None},
            {**self.__payload('pytest', 'no tests ran'), 'toolName': 'edit'},
            {**self.__payload('pytest', 'no tests ran'), 'toolArgs': 'not json'},
            {**self.__payload('pytest', 'no tests ran'), 'toolArgs': {'command': 3}},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self.__assert_silent(self.__invoke(payload))
        self.assertFalse(os.path.exists(self.__state_path))

    def test_accepts_json_string_arguments(self):
        payload = {**self.__payload('', 'no tests ran'), 'toolArgs': json.dumps({'command': 'pytest -k x'})}
        self.__assert_context(self.__invoke(payload), '`pytest -k x` ran no tests')

    def test_long_command_is_truncated(self):
        command = 'pytest ' + 'x' * 300
        context = self.__assert_context(self.__run_test(command, 'no tests ran'))
        self.assertIn(command[:200] + '...', context)
        self.assertNotIn(command, context)

    def test_sessions_are_isolated_and_command_is_not_saved(self):
        self.__assert_silent(self.__run_test('pytest', '10 passed in 0.1s'))
        other = str(uuid.uuid4())
        self.__assert_silent(self.__run_test('pytest', '5 passed in 0.1s', session_id=other))
        rows = self.__read_rows()
        self.assertEqual(len(rows), 1)
        self.assertNotIn('pytest', rows[0][0])
        self.assertEqual(len(rows[0][0]), 64)

    def test_invalid_input_reports_error(self):
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'\xff']
        base = self.__payload('pytest', 'no tests ran')
        for key, value in (('sessionId', None), ('sessionId', 'not-uuid'), ('toolName', ''), ('toolName', 3)):
            invalid.append(json.dumps({**base, key: value}).encode('utf-8'))
        without_args = dict(base)
        del without_args['toolArgs']
        invalid.append(json.dumps(without_args).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to detect vacuous test success:', result.stderr)
        self.assertFalse(os.path.exists(self.__state_path))

    def test_corrupt_state_reports_error(self):
        os.makedirs(os.path.dirname(self.__state_path))
        with open(self.__state_path, 'wb') as file:
            file.write(b'not a database')
        result = self.__run_test('pytest', 'no tests ran')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to detect vacuous test success:', result.stderr)


if __name__ == '__main__':
    run_tests()
