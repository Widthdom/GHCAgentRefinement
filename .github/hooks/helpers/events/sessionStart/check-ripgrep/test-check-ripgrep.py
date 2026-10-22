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
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
hook_path = os.path.join(script_directory, 'check-ripgrep.py')

# ファイル名にハイフンを含みimport文で読み込めないため、runpyで実行して関数などを取り出す。
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
    return exit_code, stderr.getvalue()


tests_passed = 0

with open(os.path.join(hooks_directory, 'sessionStart.json'), encoding='utf-8') as file:
    hooks = json.load(file)['hooks']['sessionStart']
hook = next(hook for hook in hooks if hook['bash'].endswith('/check-ripgrep/check-ripgrep.py'))
assert_equal(hook['bash'], 'python3 .github/hooks/helpers/events/sessionStart/check-ripgrep/check-ripgrep.py',
             'Unexpected bash command.')
assert_equal(hook['powershell'], 'python .github/hooks/helpers/events/sessionStart/check-ripgrep/check-ripgrep.py',
             'Unexpected powershell command.')
tests_passed += 1

satisfied_outputs = [
    'ripgrep 13.0.0\n-SIMD -AVX (compiled)\n',
    'ripgrep 14.1.1 (rev 4649aa9700)\n\nfeatures:+pcre2\n',
    'ripgrep 15.0.0 (rev 3a612f88b8)\n',
]
for output in satisfied_outputs:
    assert_equal(run_main('linux', output), (0, ''), f"'{output.splitlines()[0]}' must satisfy the requirement.")
    tests_passed += 1

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
for platform, install_message in install_messages.items():
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
        tests_passed += 1

# 実際のプロセスとして実行し、不正な標準入力でも動作が変わらないことと、出力のBOMなしUTF-8を検証する。
with tempfile.TemporaryDirectory() as empty_directory:
    for stdin in (b'{"sessionId":"test"}', b'not json'):
        installed = subprocess.run([sys.executable, hook_path], input=stdin, capture_output=True)
        assert_equal((installed.returncode, installed.stdout, installed.stderr), (0, b'', b''),
                     'The hook must pass silently when ripgrep is installed.')
        missing = subprocess.run([sys.executable, hook_path], input=stdin, capture_output=True,
                                 cwd=empty_directory, env={**os.environ, 'PATH': empty_directory})
        assert_equal(missing.returncode, 2, 'The hook must fail when ripgrep is not found.')
        assert_equal(missing.stdout, b'', 'The hook must not write to stdout.')
        if missing.stderr.startswith(b'\xef\xbb\xbf') or b'ripgrep 13.0.0 or later' not in missing.stderr:
            raise AssertionError(f'Unexpected stderr: {missing.stderr!r}')
        tests_passed += 1

print(f'Passed {tests_passed} check-ripgrep tests.')
