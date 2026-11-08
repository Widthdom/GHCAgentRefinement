import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'check-python.sh')
_version_check_path = os.path.abspath(
    os.path.join(_script_directory, '../../../utilities/check-python-version/check-python-version.py')
)


def _write_executable(path, content):
    with open(path, 'w', encoding='utf-8', newline='\n') as file:
        file.write(content)
    os.chmod(path, 0o755)


def _invoke_shell_hook(status=None, system='Linux'):
    shell = shutil.which('sh')
    if shell is None:
        raise AssertionError('A POSIX shell is required to test check-python.sh.')

    with tempfile.TemporaryDirectory() as directory:
        _write_executable(
            os.path.join(directory, 'uname'),
            '#!/bin/sh\nprintf "%s\\n" "$CHECK_PYTHON_SYSTEM"\n',
        )
        environment = {
            **os.environ,
            'PATH': directory,
            'CHECK_PYTHON_SYSTEM': system,
        }
        if status is not None:
            _write_executable(
                os.path.join(directory, 'python3'),
                '#!/bin/sh\nprintf "%s\\n" "$@" > "$CHECK_PYTHON_ARGS"\n'
                'printf "ignored stdout\\n"\nprintf "ignored stderr\\n" >&2\n'
                'exit "$CHECK_PYTHON_STATUS"\n',
            )
            args_path = os.path.join(directory, 'args.txt')
            environment['CHECK_PYTHON_ARGS'] = args_path
            environment['CHECK_PYTHON_STATUS'] = str(status)

        result = subprocess.run(
            [shell, _hook_path],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True, timeout=30,
        )
        args = None
        if status is not None:
            with open(args_path, encoding='utf-8') as file:
                args = file.read()
        return result.returncode, result.stdout, result.stderr, args


_failure_messages = {
    'Darwin': (
        'Python 3.14 or later is required, and it must be available as the python3 command.\n'
        'Install it with: brew install python\n'
        'If python3 is still unavailable after installation, restart the application or terminal running this hook to refresh PATH.\n'
    ),
    'Linux': (
        'Python 3.14 or later is required, and it must be available as the python3 command.\n'
        'Installation guide: https://docs.python.org/3/using/unix.html\n'
        'If python3 is still unavailable after installation, restart the application or terminal running this hook to refresh PATH.\n'
    ),
}


def _get_expected_output(failure_message):
    # 標準エラーと同じ案内を、エージェントへの指示とともにadditionalContextとして渡すこと。
    return json.dumps({
        'additionalContext': (
            f'{failure_message}At the start of your first response, tell the user about this in the language you '
            'respond in, including how to install it. Do not install it yourself.'
        ),
    }, ensure_ascii=False, separators=(',', ':')) + '\n'

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class CheckPythonTests(unittest.TestCase):
    def test_supported_versions(self):
        for version, expected_exit in (
            ((3, 13, 99), 1),
            ((3, 14, 0), 0),
            ((3, 14, 1), 0),
            ((3, 15, 0), 0),
        ):
            with self.subTest(version=version, expected_exit=expected_exit):
                with patch.object(sys, 'version_info', version), patch.object(sys, 'exit') as exit_mock:
                    runpy.run_path(_version_check_path)
                    exit_mock.assert_called_once_with(expected_exit)

    def test_platform_wrapper(self):
        if os.name != 'nt':
            return_code, output, error, args = _invoke_shell_hook(0)
            if (return_code, output, error) != (0, '', '') or os.path.abspath(args.rstrip('\n')) != _version_check_path:
                raise AssertionError(f'Unexpected successful check-python.sh result: {return_code}, {output!r}, {error!r}, {args!r}')

            for system, expected_error in _failure_messages.items():
                with self.subTest(system=system, expected_error=expected_error):
                    return_code, output, error, args = _invoke_shell_hook(1, system)
                    if ((return_code, output, error) != (0, _get_expected_output(expected_error), expected_error) or
                            os.path.abspath(args.rstrip('\n')) != _version_check_path):
                        raise AssertionError(f'Unexpected failed check-python.sh result on {system}: {return_code}, {output!r}, {error!r}, {args!r}')

            return_code, output, error, args = _invoke_shell_hook(None, 'Linux')
            if (return_code, output, error, args) != (
                0, _get_expected_output(_failure_messages['Linux']), _failure_messages['Linux'], None,
            ):
                raise AssertionError(f'Unexpected missing-python3 result: {return_code}, {output!r}, {error!r}')
        else:
            command_processor = os.environ.get('ComSpec')
            if not command_processor:
                raise AssertionError('ComSpec is required to test check-python.cmd.')

            cmd_hook_path = os.path.join(_script_directory, 'check-python.cmd')
            for code_page, status in ((932, 0), (932, 1), (65001, 0), (65001, 1)):
                with self.subTest(code_page=code_page, status=status):
                    expected_error = '' if status == 0 else (
                        'Python 3.14 or later is required, and it must be available as the python command.\n'
                        'Install it with: winget install Python.Python.3.14 --source winget\n'
                        'After installation, restart the application or terminal running this hook to refresh PATH.\n'
                    )
                    with tempfile.TemporaryDirectory() as directory:
                        helpers_directory = os.path.join(
                            directory, 'repo %CHECK_PYTHON_PATH_TOKEN% with spaces!',
                            'helpers',
                        )
                        copied_script_directory = os.path.join(
                            helpers_directory, 'events', 'sessionStart', 'check-python',
                        )
                        copied_utilities_directory = os.path.join(helpers_directory, 'utilities', 'check-python-version')
                        os.makedirs(copied_script_directory)
                        os.makedirs(copied_utilities_directory)
                        copied_hook_path = os.path.join(copied_script_directory, 'check-python.cmd')
                        copied_version_check_path = os.path.join(
                            copied_utilities_directory, 'check-python-version.py',
                        )
                        shutil.copyfile(cmd_hook_path, copied_hook_path)
                        shutil.copyfile(_version_check_path, copied_version_check_path)
                        args_path = os.path.join(directory, 'args.txt')
                        with open(os.path.join(directory, 'python.cmd'), 'w', encoding='utf-8', newline='') as file:
                            file.write(
                                '@echo off\r\n'
                                '> "%CHECK_PYTHON_ARGS%" echo %*\r\n'
                                'echo ignored stdout\r\n'
                                'echo ignored stderr >&2\r\n'
                                'exit /b %CHECK_PYTHON_STATUS%\r\n'
                            )
                        environment = {
                            **os.environ,
                            'PATH': directory + os.pathsep + os.environ.get('PATH', ''),
                            'CHECK_PYTHON_ARGS': args_path,
                            'CHECK_PYTHON_STATUS': str(status),
                            'CHECK_PYTHON_HOOK': copied_hook_path,
                            'CHECK_PYTHON_PATH_TOKEN': 'unexpected-expansion',
                        }
                        result = subprocess.run(
                            f'"{command_processor}" /d /v:off /s /c '
                            f'""%SystemRoot%\\System32\\chcp.com" {code_page} >nul & "%CHECK_PYTHON_HOOK%""',
                            env=environment,
                            capture_output=True,
                            text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=30,
                        )
                        expected_output = '' if status == 0 else _get_expected_output(expected_error)
                        if (result.returncode, result.stdout, result.stderr) != (0, expected_output, expected_error):
                            raise AssertionError(
                                f'Unexpected check-python.cmd result on CP{code_page}: {result.returncode}, '
                                f'{result.stdout!r}, {result.stderr!r}'
                            )
                        if not os.path.isfile(args_path):
                            raise AssertionError(
                                f'The python stub was not invoked: {result.returncode}, '
                                f'{result.stdout!r}, {result.stderr!r}'
                            )
                        with open(args_path, encoding='utf-8') as file:
                            args = file.read()
                        actual_version_check_path = os.path.normcase(
                            os.path.abspath(args.strip().strip('"')),
                        )
                        if actual_version_check_path != os.path.normcase(copied_version_check_path):
                            raise AssertionError(f'Unexpected command passed to python: {args!r}')


if __name__ == '__main__':
    _run_tests()
