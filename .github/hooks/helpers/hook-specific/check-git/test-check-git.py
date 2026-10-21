import contextlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
from unittest.mock import Mock, patch

script_directory = os.path.dirname(os.path.abspath(__file__))
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
hook_path = os.path.join(script_directory, 'check-git.py')
json_path = os.path.join(hooks_directory, 'check-git.json')

# ファイル名にハイフンを含みimport文で読み込めないため、runpyで実行して関数などを取り出す。
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
    return exit_code, stderr.getvalue()


tests_passed = 0

with open(json_path, encoding='utf-8') as file:
    hook = json.load(file)['hooks']['sessionStart'][0]
assert_equal(hook['bash'], 'python3 .github/hooks/helpers/hook-specific/check-git/check-git.py', 'Unexpected bash command.')
assert_equal(hook['powershell'], 'python .github/hooks/helpers/hook-specific/check-git/check-git.py',
             'Unexpected powershell command.')
tests_passed += 1

satisfied_outputs = [
    'git version 2.34.1\n',
    'git version 2.47.1.windows.2\n',
    'git version 2.39.5 (Apple Git-154)\n',
    'git version 3.0.0\n',
]
for output in satisfied_outputs:
    assert_equal(run_main('linux', output), (0, ''), f"'{output.strip()}' must satisfy the requirement.")
    tests_passed += 1

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
for platform, install_message in install_messages.items():
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
        tests_passed += 1

# 実際のプロセスとして実行し、不正な標準入力でも動作が変わらないことと、出力のBOMなしUTF-8を検証する。
with tempfile.TemporaryDirectory() as empty_directory:
    for stdin in (b'{"sessionId":"test"}', b'not json'):
        installed = subprocess.run([sys.executable, hook_path], input=stdin, capture_output=True)
        assert_equal((installed.returncode, installed.stdout, installed.stderr), (0, b'', b''),
                     'The hook must pass silently when Git is installed.')
        missing = subprocess.run([sys.executable, hook_path], input=stdin, capture_output=True,
                                 cwd=empty_directory, env={**os.environ, 'PATH': empty_directory})
        assert_equal(missing.returncode, 2, 'The hook must fail when Git is not found.')
        assert_equal(missing.stdout, b'', 'The hook must not write to stdout.')
        if missing.stderr.startswith(b'\xef\xbb\xbf') or b'Git 2.34.1 or later' not in missing.stderr:
            raise AssertionError(f'Unexpected stderr: {missing.stderr!r}')
        tests_passed += 1

print(f'Passed {tests_passed} check-git tests.')
