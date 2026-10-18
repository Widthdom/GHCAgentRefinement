import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'check-debug-mode.py')
session_id = str(uuid.uuid4())
state_path = os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement', 'debug-mode', f'{session_id}.json')


def invoke_hook_input(input_json):
    completed = subprocess.run(
        [sys.executable, hook_path],
        input=input_json.encode('utf-8'),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.returncode, completed.stdout.decode('utf-8'), completed.stderr.decode('utf-8')


def to_compressed_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})',
        value,
    )
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
    fraction = (match.group(7) or '').ljust(6, '0')[:6]
    offset_text = match.group(8)
    if offset_text == 'Z':
        offset = timedelta(0)
    else:
        sign = 1 if offset_text[0] == '+' else -1
        offset = sign * timedelta(hours=int(offset_text[1:3]), minutes=int(offset_text[4:6]))
    return datetime(year, month, day, hour, minute, second, int(fraction), tzinfo=timezone(offset))


def read_text(path):
    with open(path, 'r', encoding='utf-8', newline='') as file:
        return file.read()


valid_cases = [
    {'Name': 'regular prompt', 'Prompt': 'regular prompt', 'Enabled': False},
    {'Name': 'debug prompt', 'Prompt': '?? debug prompt', 'Enabled': True},
    {'Name': 'next regular prompt', 'Prompt': 'next regular user prompt', 'Enabled': False},
    {'Name': 'leading whitespace', 'Prompt': ' ?? debug prompt', 'Enabled': False},
    {'Name': 'embedded marker', 'Prompt': 'regular ?? prompt', 'Enabled': False},
    {'Name': 'marker only', 'Prompt': '??', 'Enabled': True},
    {'Name': 'Unicode prompt and normalized GUID', 'Prompt': '?? 日本語', 'Enabled': True,
     'SessionId': uuid.UUID(session_id).hex.upper()},
]

valid_json = to_compressed_json({'sessionId': session_id, 'prompt': '?? debug prompt'})
invalid_cases = [
    {'Name': 'empty input', 'Json': '', 'Error': 'hook input is empty'},
    {'Name': 'whitespace input', 'Json': ' \t\r\n', 'Error': 'hook input is empty'},
    {'Name': 'malformed JSON', 'Json': '{', 'Error': 'Failed to parse'},
    {'Name': 'null input', 'Json': 'null', 'Error': 'must be a JSON object'},
    {'Name': 'empty array', 'Json': '[]', 'Error': 'must be a JSON object'},
    {'Name': 'single-object array', 'Json': f'[{valid_json}]', 'Error': 'must be a JSON object'},
    {'Name': 'multiple-object array', 'Json': f'[{valid_json},{valid_json}]', 'Error': 'must be a JSON object'},
    {'Name': 'string input', 'Json': '"prompt"', 'Error': 'must be a JSON object'},
    {'Name': 'number input', 'Json': '42', 'Error': 'must be a JSON object'},
    {'Name': 'boolean input', 'Json': 'true', 'Error': 'must be a JSON object'},
    {'Name': 'missing prompt', 'Json': f'{{"sessionId":"{session_id}"}}', 'Error': 'does not contain a prompt'},
    {'Name': 'null prompt', 'Json': f'{{"sessionId":"{session_id}","prompt":null}}', 'Error': 'does not contain a prompt'},
    {'Name': 'empty prompt', 'Json': f'{{"sessionId":"{session_id}","prompt":""}}', 'Error': 'does not contain a prompt'},
    {'Name': 'whitespace prompt', 'Json': f'{{"sessionId":"{session_id}","prompt":"  "}}', 'Error': 'does not contain a prompt'},
    {'Name': 'missing session ID', 'Json': '{"prompt":"?? debug prompt"}', 'Error': 'Invalid session ID'},
    {'Name': 'invalid session ID', 'Json': '{"sessionId":"not-a-guid","prompt":"?? debug prompt"}', 'Error': 'Invalid session ID'},
    {'Name': 'unsupported session_id', 'Json': f'{{"session_id":"{session_id}","prompt":"?? debug prompt"}}', 'Error': 'Invalid session ID'},
]

try:
    for case in valid_cases:
        payload = {
            'sessionId': case.get('SessionId', session_id),
            'prompt': case['Prompt'],
        }
        started_at = datetime.now(timezone.utc)
        exit_code, output, error = invoke_hook_input(to_compressed_json(payload))
        if exit_code != 0 or error or output:
            raise AssertionError(f"Hook failed for '{case['Name']}': exit {exit_code}, stdout: {output}, stderr: {error}")

        state = json.loads(read_text(state_path))
        if not isinstance(state.get('enabled'), bool) or state['enabled'] != case['Enabled']:
            raise AssertionError(f"Unexpected debug mode for '{case['Name']}': {state.get('enabled')}")
        updated_at = parse_timestamp(state.get('updatedAt'))
        if updated_at is None:
            raise AssertionError(f"Invalid update timestamp for '{case['Name']}': {state.get('updatedAt')}")
        if updated_at < started_at or updated_at > datetime.now(timezone.utc):
            raise AssertionError(f"Invalid update timestamp for '{case['Name']}': {state.get('updatedAt')}")
        with open(state_path, 'rb') as file:
            state_bytes = file.read()
        if state_bytes[:3] == b'\xef\xbb\xbf':
            raise AssertionError(f"State file contains a UTF-8 BOM for '{case['Name']}'.")

    state_before = read_text(state_path)
    for case in invalid_cases:
        exit_code, output, error = invoke_hook_input(case['Json'])
        if exit_code != 2 or output or re.search(case['Error'], error, re.IGNORECASE) is None:
            raise AssertionError(f"Unexpected rejection for '{case['Name']}': exit {exit_code}, stdout: {output}, stderr: {error}")
        if read_text(state_path) != state_before:
            raise AssertionError(f"Invalid input changed the state file for '{case['Name']}'.")

    print(f'Passed {len(valid_cases) + len(invalid_cases)} check-debug-mode tests.')
finally:
    if os.path.exists(state_path):
        os.remove(state_path)
