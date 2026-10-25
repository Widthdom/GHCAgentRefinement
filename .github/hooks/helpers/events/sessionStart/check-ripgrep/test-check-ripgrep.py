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
hook_path = os.path.join(script_directory, 'check-ripgrep.py')
check_ripgrep = runpy.run_path(hook_path)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message} Expected '{expected}', got '{actual}'.")


def run_main(platform, output=None, error=None):
    # outputは `rg --version` の標準出力、errorは起動時に送出する例外。
    run = Mock(side_effect=error) if error else Mock(return_value=Mock(stdout=output))
    stderr = io.StringIO()
    with (
        patch.object(check_ripgrep['subprocess'], 'run', run),
        patch.object(check_ripgrep['sys'], 'platform', platform),
        patch.object(stderr, 'reconfigure', create=True),
        contextlib.redirect_stderr(stderr),
    ):
        exit_code = check_ripgrep['main']()
    run.assert_called_once_with(
        ['rg', '--version'], capture_output=True, text=True, errors='replace', check=True
    )
    return exit_code, stderr.getvalue()


satisfied_outputs = [
    'ripgrep 13.0.0\n-SIMD -AVX (compiled)\n',
    'ripgrep 14.1.1 (rev 4649aa9700)\n\nfeatures:+pcre2\n',
    'ripgrep 15.0.0 (rev 3a612f88b8)\n',
]

install_messages = {
    'win32': 'Install it with: winget install --id BurntSushi.ripgrep.MSVC --exact --source winget',
    'darwin': 'Install it with: brew install ripgrep',
    'linux': 'Installation guide: https://github.com/BurntSushi/ripgrep#installation',
}

unsatisfied_cases = [
    ('too old', {'output': 'ripgrep 12.1.1\n'}),
    ('unexpected output', {'output': 'unknown\n'}),
    ('not installed', {'error': FileNotFoundError()}),
]

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class CheckRipgrepTests(unittest.TestCase):
    def test_supported_versions(self):
        for output in satisfied_outputs:
            with self.subTest(output=output):
                assert_equal(run_main('linux', output), (0, ''), f"'{output.splitlines()[0]}' must satisfy the requirement.")

    def test_command_failure(self):
        assert_equal(
            run_main('linux', 'ripgrep 14.1.1\n', subprocess.CalledProcessError(1, ['rg', '--version'])),
            (2, 'ripgrep 13.0.0 or later is required, and it must be available as the rg command.\n'
                'Installation guide: https://github.com/BurntSushi/ripgrep#installation\n'
                'After installation, restart the application or terminal running this hook to refresh PATH.\n'),
            'A failed rg --version command must not satisfy the requirement.',
        )

    def test_installation_messages(self):
        for platform, install_message in install_messages.items():
            with self.subTest(platform=platform, install_message=install_message):
                for name, arguments in unsatisfied_cases:
                    exit_code, stderr = run_main(platform, **arguments)
                    assert_equal(exit_code, 2, f"Case '{name}' on {platform} must fail.")
                    assert_equal(
                        stderr,
                        'ripgrep 13.0.0 or later is required, and it must be available as the rg command.\n'
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
            assert_equal(missing.returncode, 2, 'The hook must fail when ripgrep is not found.')
            assert_equal(missing.stdout, b'', 'The hook must not write to stdout.')
            if missing.stderr.startswith(b'\xef\xbb\xbf') or b'ripgrep 13.0.0 or later' not in missing.stderr:
                raise AssertionError(f'Unexpected stderr: {missing.stderr!r}')


if __name__ == '__main__':
    run_tests()
