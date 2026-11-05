import os
import runpy
import sys
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'check-test-runs.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


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
        # 語中に含まれる場合はテストとみなさない。
        for command in ('npm install latest', 'git status', 'echo attest', 'contest', 'testing'):
            with self.subTest(command=command):
                self.assertFalse(is_test_command(command))

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
    run_tests()
