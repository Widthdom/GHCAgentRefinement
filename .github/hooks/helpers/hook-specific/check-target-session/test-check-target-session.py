import json
import os
import re
import subprocess
import sys

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'check-target-session.py')


def invoke_hook_input(input_json):
    completed = subprocess.run(
        [sys.executable, hook_path],
        input=input_json.encode('utf-8'),
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
]

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
]

for case in valid_cases:
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
        if parsed_output.get('systemMessage') != expected_system_message:
            raise AssertionError(f"Unexpected systemMessage for '{case['Name']}': got '{parsed_output.get('systemMessage')}'")
    elif output.strip():
        raise AssertionError(f"Expected empty output for '{case['Name']}', got: {output}")

for case in invalid_cases:
    exit_code, output, error = invoke_hook_input(case['Json'])
    if exit_code != 2:
        raise AssertionError(f"Unexpected exit code for '{case['Name']}': got {exit_code}, expected 2. stdout: {output}")
    if output:
        raise AssertionError(f"Unexpected stdout for '{case['Name']}': {output}")
    if re.search(case['Error'], error, re.IGNORECASE) is None:
        raise AssertionError(f"Unexpected error message for '{case['Name']}': got '{error}', expected pattern '{case['Error']}'")

print(f'Passed {len(valid_cases) + len(invalid_cases)} check-target-session tests.')
