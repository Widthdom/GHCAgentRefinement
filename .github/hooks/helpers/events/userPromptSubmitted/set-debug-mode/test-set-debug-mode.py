import io
import json
import os
import re
import runpy
import subprocess
import sys
import unittest
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'set-debug-mode.py')


def _to_compressed_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _parse_timestamp(value):
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


def _read_text(path):
    with open(path, 'r', encoding='utf-8', newline='') as file:
        return file.read()


_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class SetDebugModeTests(unittest.TestCase):
    def setUp(self):
        self.__temporary = tempfile.TemporaryDirectory(prefix='ghc-agent-refinement-set-debug-test-')
        self.addCleanup(self.__temporary.cleanup)
        self.test_temp_directory = self.__temporary.name
        self.__state_directory = os.path.join(self.test_temp_directory, 'ghc-agent-refinement', 'hook-states', 'debug-mode')

        self.__session_id = str(uuid.uuid4())

        self.__state_path = os.path.join(self.__state_directory, f'{self.__session_id}.json')

        self.__other_session_id = str(uuid.uuid4())

        self.__other_state_path = os.path.join(self.__state_directory, f'{self.__other_session_id}.json')

        self.__valid_cases = [
            {'Name': 'regular prompt', 'Prompt': 'regular prompt', 'Enabled': False},
            {'Name': 'debug prompt', 'Prompt': '?? debug prompt', 'Enabled': True},
            {'Name': 'next regular prompt', 'Prompt': 'next regular user prompt', 'Enabled': False},
            {'Name': 'leading whitespace', 'Prompt': ' ?? debug prompt', 'Enabled': False},
            {'Name': 'embedded marker', 'Prompt': 'regular ?? prompt', 'Enabled': False},
            {'Name': 'marker only', 'Prompt': '??', 'Enabled': True},
            {'Name': 'Unicode prompt and normalized GUID', 'Prompt': '?? 日本語', 'Enabled': True,
             'SessionId': uuid.UUID(self.__session_id).hex.upper()},
            {'Name': 'single question mark', 'Prompt': '?', 'Enabled': False},
            {'Name': 'leading newline', 'Prompt': '\n?? debug prompt', 'Enabled': False},
            {'Name': 'leading tab', 'Prompt': '\t?? debug prompt', 'Enabled': False},
            {'Name': 'full-width marker', 'Prompt': '？？ debug prompt', 'Enabled': False},
            {'Name': 'multiline debug prompt', 'Prompt': '??\n日本語', 'Enabled': True},
            {'Name': 'braced UUID', 'Prompt': '??', 'Enabled': True,
             'SessionId': '{' + self.__session_id.upper() + '}'},
            {'Name': 'padded UUID', 'Prompt': '??', 'Enabled': True,
             'SessionId': ' \t' + self.__session_id + '\r\n'},
        ]

        self.__valid_json = _to_compressed_json({'sessionId': self.__session_id, 'prompt': '?? debug prompt'})

        self.__invalid_cases = [
            {'Name': 'empty input', 'Json': '', 'Error': 'hook input is empty'},
            {'Name': 'whitespace input', 'Json': ' \t\r\n', 'Error': 'hook input is empty'},
            {'Name': 'malformed JSON', 'Json': '{', 'Error': 'Failed to parse'},
            {'Name': 'null input', 'Json': 'null', 'Error': 'must be a JSON object'},
            {'Name': 'empty array', 'Json': '[]', 'Error': 'must be a JSON object'},
            {'Name': 'single-object array', 'Json': f'[{self.__valid_json}]', 'Error': 'must be a JSON object'},
            {'Name': 'multiple-object array', 'Json': f'[{self.__valid_json},{self.__valid_json}]', 'Error': 'must be a JSON object'},
            {'Name': 'string input', 'Json': '"prompt"', 'Error': 'must be a JSON object'},
            {'Name': 'number input', 'Json': '42', 'Error': 'must be a JSON object'},
            {'Name': 'boolean input', 'Json': 'true', 'Error': 'must be a JSON object'},
            {'Name': 'missing prompt', 'Json': f'{{"sessionId":"{self.__session_id}"}}', 'Error': 'prompt must be a string'},
            {'Name': 'null prompt', 'Json': f'{{"sessionId":"{self.__session_id}","prompt":null}}', 'Error': 'prompt must be a string'},
            {'Name': 'empty prompt', 'Json': f'{{"sessionId":"{self.__session_id}","prompt":""}}', 'Error': 'prompt must not be empty or whitespace'},
            {'Name': 'whitespace prompt', 'Json': f'{{"sessionId":"{self.__session_id}","prompt":"  "}}', 'Error': 'prompt must not be empty or whitespace'},
            {'Name': 'missing session ID', 'Json': '{"prompt":"?? debug prompt"}', 'Error': 'sessionId must be a non-empty UUID string'},
            {'Name': 'invalid session ID', 'Json': '{"sessionId":"not-a-guid","prompt":"?? debug prompt"}', 'Error': 'sessionId must be a valid UUID'},
            {'Name': 'unsupported session_id', 'Json': f'{{"session_id":"{self.__session_id}","prompt":"?? debug prompt"}}', 'Error': 'sessionId must be a non-empty UUID string'},
            {'Name': 'invalid UTF-8', 'Json': b'{"prompt":"\xff"}', 'Error': 'Failed to parse'},
            {'Name': 'unescaped control character', 'Json': '{"prompt":"??\x01"}', 'Error': 'Failed to parse'},
            {'Name': 'wrong prompt casing', 'Json': _to_compressed_json({'sessionId': self.__session_id, 'Prompt': '??'}),
             'Error': 'prompt must be a string'},
            {'Name': 'wrong sessionId casing', 'Json': _to_compressed_json({'SessionId': self.__session_id, 'prompt': '??'}),
             'Error': 'sessionId must be a non-empty UUID string'},
        ]

        for value in (0, 1.5, True, False, [], ['??'], {}, {'text': '??'}):
            self.__invalid_cases.append({
                'Name': f'non-string prompt {value!r}',
                'Json': _to_compressed_json({'sessionId': self.__session_id, 'prompt': value}),
                'Error': 'prompt must be a string',
            })

        for value in ('\t\r\n', '\u3000'):
            self.__invalid_cases.append({
                'Name': f'whitespace prompt {value!r}',
                'Json': _to_compressed_json({'sessionId': self.__session_id, 'prompt': value}),
                'Error': 'prompt must not be empty or whitespace',
            })

        for value in (None, 0, True, [], [self.__session_id], {}, {'id': self.__session_id}):
            self.__invalid_cases.append({
                'Name': f'non-string sessionId {value!r}',
                'Json': _to_compressed_json({'sessionId': value, 'prompt': '??'}),
                'Error': 'sessionId must be a non-empty UUID string',
            })

        for value in ('', ' ', '\t\r\n', '\u3000'):
            self.__invalid_cases.append({
                'Name': f'empty or whitespace sessionId {value!r}',
                'Json': _to_compressed_json({'sessionId': value, 'prompt': '??'}),
                'Error': 'sessionId must be a non-empty UUID string',
            })

        for value in ('../' + self.__session_id, self.__session_id[:-1], 'g' * 32):
            self.__invalid_cases.append({
                'Name': f'invalid UUID {value!r}',
                'Json': _to_compressed_json({'sessionId': value, 'prompt': '??'}),
                'Error': 'sessionId must be a valid UUID',
            })

        result = self.__invoke_hook_input(_to_compressed_json({
            'sessionId': self.__session_id, 'prompt': '?? debug prompt',
        }))
        self.assertEqual(result, (0, '', ''))

    def __invoke_hook_input(self, input_json):
        environment = os.environ.copy()
        environment.update({
            'TMPDIR': self.test_temp_directory,
            'TMP': self.test_temp_directory,
            'TEMP': self.test_temp_directory,
        })
        completed = subprocess.run(
            [sys.executable, _hook_path],
            input=input_json if isinstance(input_json, bytes) else input_json.encode('utf-8'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment, timeout=30,
        )
        return completed.returncode, completed.stdout.decode('utf-8'), completed.stderr.decode('utf-8')

    def test_prompt_transitions(self):
        for case in self.__valid_cases:
            with self.subTest(case=case):
                payload = {
                    'sessionId': case.get('SessionId', self.__session_id),
                    'prompt': case['Prompt'],
                }
                started_at = datetime.now(timezone.utc)
                exit_code, output, error = self.__invoke_hook_input(_to_compressed_json(payload))
                if exit_code != 0 or error or output:
                    raise AssertionError(f"Hook failed for '{case['Name']}': exit {exit_code}, stdout: {output}, stderr: {error}")

                state = json.loads(_read_text(self.__state_path))
                if set(state) != {'enabled', 'updatedAt'}:
                    raise AssertionError(f"Unexpected state fields for '{case['Name']}': {state}")
                if not isinstance(state.get('enabled'), bool) or state['enabled'] != case['Enabled']:
                    raise AssertionError(f"Unexpected debug mode for '{case['Name']}': {state.get('enabled')}")
                updated_at = _parse_timestamp(state.get('updatedAt'))
                if updated_at is None:
                    raise AssertionError(f"Invalid update timestamp for '{case['Name']}': {state.get('updatedAt')}")
                if updated_at.utcoffset() != timedelta(0):
                    raise AssertionError(f"Update timestamp is not UTC for '{case['Name']}': {state.get('updatedAt')}")
                if updated_at < started_at or updated_at > datetime.now(timezone.utc):
                    raise AssertionError(f"Invalid update timestamp for '{case['Name']}': {state.get('updatedAt')}")
                with open(self.__state_path, 'rb') as file:
                    state_bytes = file.read()
                if state_bytes[:3] == b'\xef\xbb\xbf':
                    raise AssertionError(f"State file contains a UTF-8 BOM for '{case['Name']}'.")
                if b'\n' in state_bytes or b'\r' in state_bytes:
                    raise AssertionError(f"State file is not single-line JSON for '{case['Name']}'.")

    def test_invalid_input_preserves_state(self):
        state_before = _read_text(self.__state_path)

        for case in self.__invalid_cases:
            with self.subTest(case=case):
                exit_code, output, error = self.__invoke_hook_input(case['Json'])
                if exit_code != 2 or output or re.search(case['Error'], error, re.IGNORECASE) is None:
                    raise AssertionError(f"Unexpected rejection for '{case['Name']}': exit {exit_code}, stdout: {output}, stderr: {error}")
                if _read_text(self.__state_path) != state_before:
                    raise AssertionError(f"Invalid input changed the state file for '{case['Name']}'.")

    def test_session_isolation(self):
        state_before = _read_text(self.__state_path)
        exit_code, output, error = self.__invoke_hook_input(_to_compressed_json({
            'sessionId': self.__other_session_id, 'prompt': 'regular prompt',
        }))

        if exit_code != 0 or output or error:
            raise AssertionError(f'Second session failed: exit {exit_code}, stdout: {output}, stderr: {error}')

        if json.loads(_read_text(self.__other_state_path))['enabled'] is not False or _read_text(self.__state_path) != state_before:
            raise AssertionError('Updating another session changed the original session state.')

    def test_subagent_prompt_preserves_state(self):
        # サブエージェントへの依頼は親と同じsessionIdで届くため、親が有効にしたデバッグモードを解除しない。
        state_before = _read_text(self.__state_path)
        exit_code, output, error = self.__invoke_hook_input(_to_compressed_json({
            'sessionId': self.__session_id, 'prompt': 'regular subagent prompt',
            'agentId': 'ff218530-a3e9-491e-a532-25b960bf6e9b', 'agentType': 'explore',
        }))

        if exit_code != 0 or output or error:
            raise AssertionError(f'Subagent prompt failed: exit {exit_code}, stdout: {output}, stderr: {error}')
        if _read_text(self.__state_path) != state_before or json.loads(state_before)['enabled'] is not True:
            raise AssertionError('Subagent prompt changed the parent debug mode state.')

    def test_input_read_failure(self):
        state_before = _read_text(self.__state_path)
        hook_module = runpy.run_path(_hook_path)

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
            if exit_code != 2 or error != 'Failed to parse userPromptSubmitted hook input: test read failure\n':
                raise AssertionError(f'Unexpected read error handling: exit {exit_code}, stderr: {error}')
            stdout.write.assert_not_called()

        if _read_text(self.__state_path) != state_before:
            raise AssertionError('Input read failure changed the state file.')


if __name__ == '__main__':
    _run_tests()
