import contextlib
import io
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'check-git.py')
check_git = runpy.run_path(hook_path)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message} Expected '{expected}', got '{actual}'.")


def run_main(platform, output=None, error=None):
    # outputは `git --version` の標準出力、errorは起動時に送出する例外。
    run = Mock(side_effect=error) if error else Mock(return_value=Mock(stdout=output))
    stderr = io.StringIO()
    with (
        patch.object(check_git['subprocess'], 'run', run),
        patch.object(check_git['sys'], 'platform', platform),
        patch.object(stderr, 'reconfigure', create=True),
        contextlib.redirect_stderr(stderr),
    ):
        exit_code = check_git['main']()
    run.assert_called_once_with(
        ['git', '--version'], capture_output=True, text=True, errors='replace', check=True
    )
    return exit_code, stderr.getvalue()


satisfied_outputs = [
    'git version 2.34.1\n',
    'git version 2.47.1.windows.2\n',
    'git version 2.39.5 (Apple Git-154)\n',
    'git version 3.0.0\n',
]

install_messages = {
    'win32': 'Install it with: winget install --id Git.Git --exact --source winget',
    'darwin': 'Install it with: brew install git',
    'linux': 'Installation guide: https://git-scm.com/install/linux',
}

unsatisfied_cases = [
    ('too old', {'output': 'git version 2.34.0\n'}),
    ('unexpected output', {'output': 'unknown\n'}),
    ('not installed', {'error': FileNotFoundError()}),
]

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests'))
from hook_test_output import run_tests


class CheckGitTests(unittest.TestCase):
    def test_supported_versions(self):
        for output in satisfied_outputs:
            with self.subTest(output=output):
                assert_equal(run_main('linux', output), (0, ''), f"'{output.strip()}' must satisfy the requirement.")

    def test_command_failure(self):
        assert_equal(
            run_main('linux', 'git version 2.47.1\n', subprocess.CalledProcessError(1, ['git', '--version'])),
            (2, 'Git 2.34.1 or later is required, and it must be available as the git command.\n'
                'Installation guide: https://git-scm.com/install/linux\n'
                'After installation, restart the application or terminal running this hook to refresh PATH.\n'),
            'A failed git --version command must not satisfy the requirement.',
        )

    def test_installation_messages(self):
        for platform, install_message in install_messages.items():
            with self.subTest(platform=platform, install_message=install_message):
                for name, arguments in unsatisfied_cases:
                    exit_code, stderr = run_main(platform, **arguments)
                    assert_equal(exit_code, 2, f"Case '{name}' on {platform} must fail.")
                    assert_equal(
                        stderr,
                        'Git 2.34.1 or later is required, and it must be available as the git command.\n'
                        f'{install_message}\n'
                        'After installation, restart the application or terminal running this hook to refresh PATH.\n',
                        f"Case '{name}' on {platform} had an unexpected message.",
                    )

    def test_missing_command_process(self):
        with tempfile.TemporaryDirectory() as empty_directory:
            missing = subprocess.run(
                [sys.executable, hook_path], input=b'{"sessionId":"test"}', capture_output=True,
                cwd=empty_directory, env={**os.environ, 'PATH': empty_directory},
            )
            assert_equal(missing.returncode, 2, 'The hook must fail when Git is not found.')
            assert_equal(missing.stdout, b'', 'The hook must not write to stdout.')
            if missing.stderr.startswith(b'\xef\xbb\xbf') or b'Git 2.34.1 or later' not in missing.stderr:
                raise AssertionError(f'Unexpected stderr: {missing.stderr!r}')


if __name__ == '__main__':
    run_tests()
