import contextlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'check-jq.py')
check_jq = runpy.run_path(hook_path)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message} Expected '{expected}', got '{actual}'.")


def run_main(platform, output=None, error=None):
    # outputは `jq --version` の標準出力、errorは起動時に送出する例外。
    run = Mock(side_effect=error) if error else Mock(return_value=Mock(stdout=output))
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        patch.object(check_jq['subprocess'], 'run', run),
        patch.object(check_jq['sys'], 'platform', platform),
        patch.object(stdout, 'reconfigure', create=True),
        patch.object(stderr, 'reconfigure', create=True),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        exit_code = check_jq['main']()
    run.assert_called_once_with(
        ['jq', '--version'], capture_output=True, text=True, errors='replace', check=True
    )
    return exit_code, stderr.getvalue(), stdout.getvalue()


def assert_notified(result, install_message, message):
    # 案内を標準エラーに出し、同じ案内をadditionalContextでエージェントに渡して正常終了すること。
    exit_code, stderr, stdout = result
    expected = (
        'jq 1.6 or later is required, and it must be available as the jq command.\n'
        f'{install_message}\n'
        'After installation, restart the application or terminal running this hook to refresh PATH.'
    )
    assert_equal(exit_code, 0, f'{message} The hook must not fail.')
    assert_equal(stderr, f'{expected}\n', f'{message} Unexpected stderr.')
    assert_equal(
        json.loads(stdout), {'additionalContext': check_jq['ADDITIONAL_CONTEXT'].format(message=expected)},
        f'{message} Unexpected stdout.',
    )


satisfied_outputs = [
    'jq-1.6\n',
    'jq-1.7.1\n',
    'jq-1.8.0-dirty\n',
    'jq-2.0\n',
]

install_messages = {
    'win32': 'Install it with: winget install --id jqlang.jq --exact --source winget',
    'darwin': 'Install it with: brew install jq',
    'linux': 'Installation guide: https://github.com/jqlang/jq#installation',
}

unsatisfied_cases = [
    ('too old', {'output': 'jq-1.5-1-a5b5cbe\n'}),
    ('unexpected output', {'output': 'unknown\n'}),
    ('not installed', {'error': FileNotFoundError()}),
]

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class CheckJqTests(unittest.TestCase):
    def test_supported_versions(self):
        for output in satisfied_outputs:
            with self.subTest(output=output):
                assert_equal(run_main('linux', output), (0, '', ''), f"'{output.splitlines()[0]}' must satisfy the requirement.")

    def test_command_failure(self):
        assert_notified(
            run_main('linux', 'jq-1.7.1\n', subprocess.CalledProcessError(1, ['jq', '--version'])),
            install_messages['linux'],
            'A failed jq --version command must not satisfy the requirement.',
        )

    def test_installation_messages(self):
        for platform, install_message in install_messages.items():
            with self.subTest(platform=platform, install_message=install_message):
                for name, arguments in unsatisfied_cases:
                    assert_notified(run_main(platform, **arguments), install_message, f"Case '{name}' on {platform}:")

    def test_missing_command_process(self):
        with tempfile.TemporaryDirectory() as empty_directory:
            missing = subprocess.run(
                [sys.executable, hook_path], input=b'{"sessionId":"test"}', capture_output=True,
                cwd=empty_directory, env={**os.environ, 'PATH': empty_directory},
            )
            assert_equal(missing.returncode, 0, 'The hook must not fail when jq is not found.')
            for name, output in (('stdout', missing.stdout), ('stderr', missing.stderr)):
                if output.startswith(b'\xef\xbb\xbf') or b'jq 1.6 or later' not in output:
                    raise AssertionError(f'Unexpected {name}: {output!r}')
            if 'additionalContext' not in json.loads(missing.stdout.decode('utf-8')):
                raise AssertionError(f'Unexpected stdout: {missing.stdout!r}')


if __name__ == '__main__':
    run_tests()
