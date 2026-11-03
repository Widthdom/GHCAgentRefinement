import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'load-copilot-instructions.py')
_github_directory = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
)

_real_instructions_path = os.path.join(_github_directory, 'copilot-instructions.md')


def _invoke_hook(script_path, *, cwd=None, env=None):
    # BOMの有無を検査できるよう、標準出力はバイト列のまま受け取る。
    completed = subprocess.run(
        [sys.executable, script_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    return {
        'ExitCode': completed.returncode,
        'OutputBytes': completed.stdout,
        'Output': completed.stdout.decode('utf-8'),
        'Error': completed.stderr.decode('utf-8'),
    }


def _new_hook_sandbox(suffix=''):
    root = os.path.join(tempfile.gettempdir(), f'ghc-agent-refinement-test-{uuid.uuid4().hex}{suffix}')
    hook_directory = os.path.join(
        root, '.github', 'hooks', 'helpers', 'events', 'sessionStart', 'load-copilot-instructions'
    )
    os.makedirs(hook_directory, exist_ok=True)
    shutil.copy(_hook_path, hook_directory)
    return {
        'Root': root,
        'ScriptPath': os.path.join(hook_directory, 'load-copilot-instructions.py'),
        'InstructionsPath': os.path.join(root, '.github', 'copilot-instructions.md'),
    }


def _assert_valid_output(name, result, expected_context):
    if result['ExitCode'] != 0 or result['Error']:
        raise AssertionError(f"Hook failed for '{name}': exit {result['ExitCode']}, stderr: {result['Error']}")
    if result['OutputBytes'][:3] == b'\xef\xbb\xbf':
        raise AssertionError(f"Output contains a UTF-8 BOM for '{name}'.")
    if re.fullmatch(r'[^\r\n]+\r?\n', result['Output']) is None:
        raise AssertionError(f"Output is not a single JSON line for '{name}': {result['Output']}")
    output = json.loads(result['Output'])
    if not isinstance(output, dict):
        raise AssertionError(f"Output is not a JSON object for '{name}': {result['Output']}")
    property_names = list(output.keys())
    if len(property_names) != 1 or property_names[0] != 'additionalContext':
        raise AssertionError(f"Unexpected output properties for '{name}': {', '.join(property_names)}")
    if output['additionalContext'] != expected_context:
        raise AssertionError(f"additionalContext does not match the instructions file for '{name}'.")


_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class LoadCopilotInstructionsTests(unittest.TestCase):
    def __new_hook_sandbox(self, suffix=''):
        sandbox = _new_hook_sandbox(suffix)
        self.addCleanup(shutil.rmtree, sandbox['Root'])
        return sandbox

    def test_real_instructions(self):
        with open(_real_instructions_path, 'rb') as file:
            expected = file.read().decode('utf-8-sig')

        _assert_valid_output('real instructions file', _invoke_hook(_hook_path), expected)

    def test_content_preservation(self):
        valid_cases = [
            {'Name': 'Japanese text', 'Content': '# 指示\n- 応答は日本語を用いる。\n', 'Bytes': None},
            {'Name': 'CRLF line endings', 'Content': 'line1\r\nline2\r\n', 'Bytes': None},
            {'Name': 'mixed line endings', 'Content': 'line1\rline2\r\nline3\n', 'Bytes': None},
            {'Name': 'leading and trailing whitespace is preserved', 'Content': ' \t\n指示\r\n \t', 'Bytes': None},
            {'Name': 'Unicode separators', 'Content': 'a\u0085b\u2028c\u2029d', 'Bytes': None},
            {'Name': 'embedded control characters', 'Content': 'a\x00\x1cb', 'Bytes': None},
            {'Name': 'embedded BOM is preserved', 'Content': 'a\ufeffb', 'Bytes': None},
            {'Name': 'JSON special characters', 'Content': "quote \" backslash \\ tab \t slash / <tag> & 'single'", 'Bytes': None},
            {'Name': 'emoji (surrogate pair)', 'Content': '絵文字 😀 を含む', 'Bytes': None},
            {'Name': 'UTF-8 BOM is not part of the content', 'Content': '# 指示\n',
             'Bytes': b'\xef\xbb\xbf' + '# 指示\n'.encode('utf-8')},
        ]

        for case in valid_cases:
            with self.subTest(case=case):
                sandbox = self.__new_hook_sandbox()
                data = case['Bytes'] if case['Bytes'] is not None else case['Content'].encode('utf-8')
                with open(sandbox['InstructionsPath'], 'wb') as file:
                    file.write(data)
                _assert_valid_output(case['Name'], _invoke_hook(sandbox['ScriptPath']), case['Content'])

    def test_invalid_files(self):
        invalid_cases = [
            {'Name': 'missing instructions file', 'Bytes': None, 'Error': 'instructions file not found'},
            {'Name': 'empty instructions file', 'Bytes': b'', 'Error': 'instructions file is empty'},
            {'Name': 'whitespace-only instructions file', 'Bytes': ' \t\r\n'.encode('utf-8'), 'Error': 'instructions file is empty'},
            {'Name': 'Unicode whitespace-only instructions file',
             'Bytes': '\u0085\u00a0\u2028\u2029\u3000'.encode('utf-8'), 'Error': 'instructions file is empty'},
            {'Name': 'Python whitespace control characters',
             'Bytes': b'\x1c\x1d\x1e\x1f', 'Error': 'instructions file is empty'},
            {'Name': 'BOM-only instructions file', 'Bytes': b'\xef\xbb\xbf', 'Error': 'instructions file is empty'},
        ]

        for case in invalid_cases:
            with self.subTest(case=case):
                sandbox = self.__new_hook_sandbox()
                if case['Bytes'] is not None:
                    with open(sandbox['InstructionsPath'], 'wb') as file:
                        file.write(case['Bytes'])
                result = _invoke_hook(sandbox['ScriptPath'])
                if result['ExitCode'] != 2 or result['Output'] or re.search(case['Error'], result['Error'], re.IGNORECASE) is None:
                    raise AssertionError(
                        f"Unexpected result for '{case['Name']}': exit {result['ExitCode']}, "
                        f"stdout: {result['Output']}, stderr: {result['Error']}")

    def test_directory_instead_of_file(self):
        sandbox = self.__new_hook_sandbox()

        os.mkdir(sandbox['InstructionsPath'])

        result = _invoke_hook(sandbox['ScriptPath'])

        if result['ExitCode'] != 2 or result['Output'] or \
                re.search('instructions file not found', result['Error'], re.IGNORECASE) is None:
            raise AssertionError(
                f"Unexpected result for 'directory instead of file': exit {result['ExitCode']}, "
                f"stdout: {result['Output']}, stderr: {result['Error']}")

    def test_encoding_errors(self):
        encoding_error_cases = [
            ('invalid UTF-8', b'\xff'),
            ('truncated UTF-8', b'\xe3\x81'),
            ('UTF-8 encoded surrogate', b'\xed\xa0\x80'),
            ('UTF-16 LE BOM', b'\xff\xfe' + '指示'.encode('utf-16-le')),
            ('UTF-16 BE BOM', b'\xfe\xff' + '指示'.encode('utf-16-be')),
            ('UTF-32 LE BOM', b'\xff\xfe\x00\x00' + '指示'.encode('utf-32-le')),
            ('UTF-32 BE BOM', b'\x00\x00\xfe\xff' + '指示'.encode('utf-32-be')),
        ]

        for name, data in encoding_error_cases:
            with self.subTest(name=name, data=data):
                sandbox = self.__new_hook_sandbox()
                with open(sandbox['InstructionsPath'], 'wb') as file:
                    file.write(data)
                result = _invoke_hook(sandbox['ScriptPath'])
                if result['ExitCode'] != 1 or result['Output'] or 'UnicodeDecodeError' not in result['Error']:
                    raise AssertionError(
                        f"Unexpected result for '{name}': exit {result['ExitCode']}, "
                        f"stdout: {result['Output']}, stderr: {result['Error']}")

    def test_unicode_paths_and_streams(self):
        sandbox = self.__new_hook_sandbox('-日本語 path')


        content = '# 指示 😀\r\n'

        with open(sandbox['InstructionsPath'], 'wb') as file:
            file.write(content.encode('utf-8'))

        ascii_env = os.environ.copy()

        ascii_env['PYTHONIOENCODING'] = 'ascii:strict'

        _assert_valid_output(
            'UTF-8 stdout with ASCII default and unrelated working directory',
            _invoke_hook(sandbox['ScriptPath'], cwd=tempfile.gettempdir(), env=ascii_env),
            content,
        )


        relative_script_path = os.path.relpath(sandbox['ScriptPath'], sandbox['Root'])

        _assert_valid_output(
            'relative script path',
            _invoke_hook(relative_script_path, cwd=sandbox['Root']),
            content,
        )


        os.remove(sandbox['InstructionsPath'])

        result = _invoke_hook(sandbox['ScriptPath'], env=ascii_env)

        expected_error = f"Copilot instructions file not found: {sandbox['InstructionsPath']}"

        if result['ExitCode'] != 2 or result['Output'] or result['Error'].rstrip('\r\n') != expected_error:
            raise AssertionError(
                f"Unexpected UTF-8 stderr with ASCII default: exit {result['ExitCode']}, "
                f"stdout: {result['Output']}, stderr: {result['Error']}")


if __name__ == '__main__':
    run_tests()
