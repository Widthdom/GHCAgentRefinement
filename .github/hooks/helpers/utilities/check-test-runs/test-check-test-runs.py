import os
import re
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'check-test-runs.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class CheckTestRunsTests(unittest.TestCase):
    def test_test_commands(self):
        is_test_command = _utility['is_test_command']
        for command in (
            'python3 .github/hooks/tests/test-all-hooks.py', 'npm test', 'pytest -q', 'python -m unittest',
            'npx jest', 'npx vitest run', 'mocha', 'bundle exec rspec', 'vendor/bin/phpunit', 'ctest',
            'tox -e py314', 'Invoke-Pester', 'dotnet test', 'go test ./...', 'cargo test', 'PYTEST',
            'npm run test:unit',
        ):
            with self.subTest(command=command):
                self.assertTrue(is_test_command(command))
        # 語中に含まれる場合や、読むだけのコマンド・移動の引数にある場合はテストとみなさない。
        for command in (
            'npm install latest', 'git status', 'echo attest', 'contest', 'testing',
            'ls tests', 'git log --oneline | grep test', 'cat .github/hooks/test-x.py', 'git add test-x.py',
            'cd tests', 'Get-Content tests\\a.ps1',
        ):
            with self.subTest(command=command):
                self.assertFalse(is_test_command(command))
        for command in ('cd tests && pytest', 'FOO=1 timeout 60 npm test', 'python .github\\hooks\\tests\\test-all-hooks.py'):
            with self.subTest(command=command):
                self.assertTrue(is_test_command(command))

    def test_test_result_reported(self):
        is_test_result_reported = _utility['is_test_result_reported']
        for command in (
            'pytest', 'cd x; pytest', 'pytest && echo ok', '(cd x && pytest)', 'pytest > /tmp/log 2>&1',
            'set -o pipefail; pytest | tail', 'set -e; pytest; echo done', 'pytest && a | b',
            'set -euo pipefail\npytest | tail',
        ):
            with self.subTest(command=command):
                self.assertTrue(is_test_result_reported(command))
        for command in (
            'python3 test-x.py; echo done', 'python3 test-x.py || true', 'pytest | tail', 'pytest &',
            'pytest\necho done', '(cd x && pytest) || true', 'pytest -k a; pytest -k b', 'ls tests',
            'set +o pipefail; pytest | tail', 'echo pipefail; pytest | tail',
            'set -o pipefail; set +o pipefail; pytest | tail', 'pytest | tail; set -o pipefail',
            'echo x | set -o pipefail; pytest | tail',
        ):
            with self.subTest(command=command):
                self.assertFalse(is_test_result_reported(command))

    def test_custom_pattern_and_runs_command(self):
        pattern = re.compile(r'test-a\.py')
        self.assertTrue(_utility['is_test_command']('python3 test-a.py; echo', pattern))
        self.assertFalse(_utility['is_test_command']('pytest', pattern))
        self.assertFalse(_utility['is_test_result_reported']('python3 test-a.py; echo', pattern))
        self.assertTrue(_utility['runs_command'](['sudo', 'FOO=1', 'python3', 'test-a.py'], pattern))
        self.assertFalse(_utility['runs_command'](['grep', 'test-a.py'], pattern))
        self.assertEqual(_utility['get_command_words'](['timeout', '60', 'env', '-i', 'pytest']), ['pytest'])

    def test_compile_command_pattern(self):
        pattern = _utility['compile_command_pattern']((*_utility['TEST_COMMAND_WORDS'], 'build', 'lint'))
        for command, expected in (('npm run build', True), ('eslint .', False), ('npm run lint', True),
                                  ('pytest', True), ('rebuild', False)):
            with self.subTest(command=command):
                self.assertEqual(pattern.search(command) is not None, expected)

    def test_test_pass_claims(self):
        claims_tests_passed = _utility['claims_tests_passed']
        for response in (
            'テストが通りました。', 'すべてのテストに成功した。', 'テストはパスしています。', 'テスト通過',
            'All tests passed.', 'Tests are now passing.', 'tests succeeded', 'The test passes.',
            '「テスト0件成功」は未確認。今回はテストが通った。',
            '`All tests passed` is an example. Tests passed.',
            '> Tests passed.\nTests passed.',
            'テストは「unit」を含めて通過した。',
        ):
            with self.subTest(response=response):
                self.assertTrue(claims_tests_passed(response))
        for response in (
            'テストが通っていない。', 'テストは成功しなかった。', 'テストはパスしません。', 'テストを追加した。',
            'Tests failed.', 'I will run the tests.',
            # コードブロック内の出力例は主張とみなさない。
            '結果は次のとおり。\n```\nAll tests passed.\n```\n', '~~~\nテストが通りました\n~~~\n',
            '「テスト0件成功」は未確認の条件で、今回実行したという報告ではない。',
            '『テストが通った』という表現を検査する。',
            '`テスト0件成功` の条件は未確認。',
            '"All tests passed" is an example, not a result.',
            '“Tests passed” is quoted output.',
            '> All tests passed.\nこの条件は未確認。',
            'テスト\n```\n出力例\n```\n成功',
        ):
            with self.subTest(response=response):
                self.assertFalse(claims_tests_passed(response))


if __name__ == '__main__':
    _run_tests()
