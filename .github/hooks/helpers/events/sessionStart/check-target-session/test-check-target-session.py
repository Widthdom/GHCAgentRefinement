import io
import json
import os
import re
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'check-target-session.py')


def invoke_hook_input(input_json):
    completed = subprocess.run(
        [sys.executable, hook_path],
        input=input_json if isinstance(input_json, bytes) else input_json.encode('utf-8'),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.returncode, completed.stdout.decode('utf-8'), completed.stderr.decode('utf-8')


expected_system_message = (
    "This session is using the VS Code Local harness. Open the Agents window, create a new session, "
    "and select Copilot as the Session Target to use this repository's Copilot CLI-compatible hooks. "
    "Continue this task in that new session."
)

valid_cases = [
    {
        'Name': 'Local harness sessionStart',
        'Json': '{"hook_event_name":"SessionStart"}',
        'ExpectMessage': True,
    },
    {
        'Name': 'Copilot CLI session payload (missing hook_event_name)',
        'Json': '{"sessionId":"00000000-0000-0000-0000-000000000000"}',
        'ExpectMessage': False,
    },
    {
        'Name': 'Different event name',
        'Json': '{"hook_event_name":"UserPrompt"}',
        'ExpectMessage': False,
    },
    {
        'Name': 'Case-sensitive event mismatch',
        'Json': '{"hook_event_name":"sessionstart"}',
        'ExpectMessage': False,
    },
    {
        'Name': 'Hook configuration event casing',
        'Json': '{"hook_event_name":"sessionStart"}',
        'ExpectMessage': False,
    },
    {
        'Name': 'Empty object',
        'Json': '{}',
        'ExpectMessage': False,
    },
    {
        'Name': 'Case-sensitive property mismatch',
        'Json': '{"HOOK_EVENT_NAME":"SessionStart"}',
        'ExpectMessage': False,
    },
    {
        'Name': 'Unicode payload with surrounding whitespace',
        'Json': ' \r\n{"hook_event_name":"SessionStart","cwd":"日本語"}\r\n ',
        'ExpectMessage': True,
    },
]

for value in (None, True, False, 0, 1.5, '', ' SessionStart ', [], ['SessionStart'],
              ['SessionStart', 'SessionStart'], {}, {'name': 'SessionStart'}):
    valid_cases.append({
        'Name': f'Non-matching event value {value!r}',
        'Json': json.dumps({'hook_event_name': value}),
        'ExpectMessage': False,
    })

invalid_cases = [
    {'Name': 'empty input', 'Json': '', 'Error': r'sessionStart hook input is empty\.'},
    {'Name': 'whitespace input', 'Json': ' \t\r\n', 'Error': r'sessionStart hook input is empty\.'},
    {'Name': 'malformed JSON', 'Json': '{', 'Error': 'Failed to parse sessionStart hook input:'},
    {'Name': 'null input', 'Json': 'null', 'Error': r'sessionStart hook input must be a JSON object\.'},
    {'Name': 'empty array', 'Json': '[]', 'Error': r'sessionStart hook input must be a JSON object\.'},
    {'Name': 'single-object array', 'Json': '[{"hook_event_name":"SessionStart"}]', 'Error': r'sessionStart hook input must be a JSON object\.'},
    {'Name': 'multiple-object array', 'Json': '[{"hook_event_name":"SessionStart"},{"hook_event_name":"SessionStart"}]', 'Error': r'sessionStart hook input must be a JSON object\.'},
    {'Name': 'string input', 'Json': '"SessionStart"', 'Error': r'sessionStart hook input must be a JSON object\.'},
    {'Name': 'number input', 'Json': '42', 'Error': r'sessionStart hook input must be a JSON object\.'},
    {'Name': 'boolean input', 'Json': 'true', 'Error': r'sessionStart hook input must be a JSON object\.'},
    {'Name': 'invalid UTF-8', 'Json': b'{"hook_event_name":"SessionStart","value":"\xff"}',
     'Error': 'Failed to parse sessionStart hook input:'},
    {'Name': 'unescaped control character', 'Json': '{"hook_event_name":"SessionStart","value":"raw\nnewline"}',
     'Error': 'Failed to parse sessionStart hook input:'},
    {'Name': 'trailing content', 'Json': '{"hook_event_name":"SessionStart"} trailing',
     'Error': 'Failed to parse sessionStart hook input:'},
]

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests'))
from hook_test_output import run_tests


class CheckTargetSessionTests(unittest.TestCase):
    def test_valid_input(self):
        for case in valid_cases:
            with self.subTest(case=case):
                exit_code, output, error = invoke_hook_input(case['Json'])
                if exit_code != 0:
                    raise AssertionError(f"Unexpected exit code for '{case['Name']}': got {exit_code}, expected 0. stderr: {error}")
                if error:
                    raise AssertionError(f"Unexpected stderr for '{case['Name']}': {error}")

                if case['ExpectMessage']:
                    try:
                        parsed_output = json.loads(output)
                    except ValueError:
                        raise AssertionError(f"Failed to parse stdout as JSON for '{case['Name']}': {output}")
                    if parsed_output != {'systemMessage': expected_system_message}:
                        raise AssertionError(f"Unexpected JSON output for '{case['Name']}': {parsed_output}")
                    if output.rstrip('\r\n') != json.dumps(parsed_output, ensure_ascii=False, separators=(',', ':')):
                        raise AssertionError(f"Expected compact single-line JSON for '{case['Name']}': {output!r}")
                elif output:
                    raise AssertionError(f"Expected empty output for '{case['Name']}', got: {output}")

    def test_invalid_input(self):
        for case in invalid_cases:
            with self.subTest(case=case):
                exit_code, output, error = invoke_hook_input(case['Json'])
                if exit_code != 2:
                    raise AssertionError(f"Unexpected exit code for '{case['Name']}': got {exit_code}, expected 2. stdout: {output}")
                if output:
                    raise AssertionError(f"Unexpected stdout for '{case['Name']}': {output}")
                if re.search(case['Error'], error, re.IGNORECASE) is None:
                    raise AssertionError(f"Unexpected error message for '{case['Name']}': got '{error}', expected pattern '{case['Error']}'")

    def test_input_read_failure(self):
        hook_module = runpy.run_path(hook_path)

        with (
            patch.object(sys, 'stdin') as stdin,
            patch.object(sys, 'stdout') as stdout,
            io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='') as stderr,
            patch.object(sys, 'stderr', stderr),
        ):
            stdin.buffer.read.side_effect = OSError('test read failure')
            exit_code = hook_module['main']()
            stderr.flush()
            error = stderr.buffer.getvalue().decode('utf-8')
            if exit_code != 2 or error != 'Failed to parse sessionStart hook input: test read failure\n':
                raise AssertionError(f'Unexpected read error handling: exit {exit_code}, stderr: {error}')
            stdout.write.assert_not_called()


if __name__ == '__main__':
    run_tests()
