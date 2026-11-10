import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import threading
import time
import uuid
from datetime import datetime
from unittest.mock import Mock, patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'debug-add-response-footer.py')
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))), 'utilities')


def _to_compressed_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _write_all_text(path, text):
    with open(path, 'w', encoding='utf-8', newline='') as file:
        file.write(text)


def _new_session_id():
    return str(uuid.uuid4())


_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class DebugResponseFooterTests(unittest.TestCase):
    def setUp(self):
        self.__temporary = tempfile.TemporaryDirectory(prefix='ghc-agent-refinement-footer-test-')
        self.addCleanup(self.__temporary.cleanup)
        self.__temp_dir = self.__temporary.name
        self.__debug_state_dir = os.path.join(self.__temp_dir, 'ghc-agent-refinement', 'hook-states', 'debug-mode')
        os.makedirs(self.__debug_state_dir)
        self.__enabled_session_id = _new_session_id()
        self.__enabled_state_path = os.path.join(self.__debug_state_dir, f'{self.__enabled_session_id}.json')
        _write_all_text(self.__enabled_state_path, _to_compressed_json({'enabled': True}))
        self.__valid_transcript = os.path.join(self.__temp_dir, 'valid-transcript.jsonl')
        _write_all_text(self.__valid_transcript, _to_compressed_json({
            'type': 'assistant.message', 'data': {'content': 'Hello world'},
        }) + '\n')
        self.__valid_block_json = _to_compressed_json({
            'sessionId': self.__enabled_session_id, 'transcriptPath': self.__valid_transcript,
        })

    def __invoke_hook_input(self, input_json):
        environment = os.environ.copy()
        environment.update({
            'TMPDIR': self.__temp_dir,
            'TMP': self.__temp_dir,
            'TEMP': self.__temp_dir,
        })
        completed = subprocess.run(
            [sys.executable, _hook_path],
            input=input_json if isinstance(input_json, bytes) else input_json.encode('utf-8'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment, timeout=30,
        )
        return completed.returncode, completed.stdout.decode('utf-8'), completed.stderr.decode('utf-8')

    def test_footer_workflow(self):
        invalid_inputs = [
            {'Name': 'Empty input', 'Json': '', 'Error': 'Failed to parse agentStop hook input: hook input is empty.'},
            {'Name': 'Whitespace input', 'Json': '   \t\n', 'Error': 'Failed to parse agentStop hook input: hook input is empty.'},
            {'Name': 'Invalid JSON', 'Json': '{invalid-json}', 'Error': 'Failed to parse agentStop hook input:'},
            {'Name': 'Invalid UTF-8', 'Json': b'{"sessionId":"\xff"}', 'Error': 'Failed to parse agentStop hook input:'},
            {'Name': 'Array JSON', 'Json': '[]', 'Error': 'Failed to parse agentStop hook input: hook input must be a JSON object.'},
            {'Name': 'String JSON', 'Json': '"not-an-object"', 'Error': 'Failed to parse agentStop hook input: hook input must be a JSON object.'},
            {'Name': 'Null JSON', 'Json': 'null', 'Error': 'Failed to parse agentStop hook input: hook input must be a JSON object.'},
            {'Name': 'Boolean JSON', 'Json': 'true', 'Error': 'Failed to parse agentStop hook input: hook input must be a JSON object.'},
            {'Name': 'Single object array', 'Json': '[{}]', 'Error': 'Failed to parse agentStop hook input: hook input must be a JSON object.'},
        ]

        for case in invalid_inputs:
            with self.subTest(case=case):
                exit_code, output, error = self.__invoke_hook_input(case['Json'])
                if exit_code != 2 or output or case['Error'] not in error:
                    raise AssertionError(f"Expected exit 2 for '{case['Name']}', got {exit_code}. Output: {output}, Error: {error}")

        valid_guid = _new_session_id()

        missing_path_cases = [
            {'Name': 'Missing transcriptPath', 'Json': _to_compressed_json({'sessionId': valid_guid}),
             'Error': 'Failed to parse agentStop hook input: transcriptPath must be a non-empty string.'},
            {'Name': 'Whitespace transcriptPath', 'Json': _to_compressed_json({'sessionId': valid_guid, 'transcriptPath': '   '}),
             'Error': 'Failed to parse agentStop hook input: transcriptPath must be a non-empty string.'},
            {'Name': 'Nonexistent transcriptPath', 'Json': _to_compressed_json(
                {'sessionId': valid_guid, 'transcriptPath': os.path.join(self.__temp_dir, 'not-found.jsonl')}),
             'Error': 'Failed to parse agentStop hook input: transcript path is not a file'},
            {'Name': 'Directory transcriptPath', 'Json': _to_compressed_json({'sessionId': valid_guid, 'transcriptPath': self.__temp_dir}),
             'Error': 'Failed to parse agentStop hook input: transcript path is not a file'},
            {'Name': 'Non-string snake_case transcript_path', 'Json': _to_compressed_json(
                {'sessionId': valid_guid, 'transcript_path': []}),
             'Error': 'Failed to parse agentStop hook input: transcriptPath must be a non-empty string.'},
            {'Name': 'Non-string transcriptPath', 'Json': _to_compressed_json({'sessionId': valid_guid, 'transcriptPath': []}),
             'Error': 'Failed to parse agentStop hook input: transcriptPath must be a non-empty string.'},
            {'Name': 'Invalid transcriptPath with valid snake_case path', 'Json': _to_compressed_json(
                {'sessionId': valid_guid, 'transcriptPath': None, 'transcript_path': self.__valid_transcript}),
             'Error': 'Failed to parse agentStop hook input: transcriptPath must be a non-empty string.'},
            {'Name': 'Missing sessionId', 'Json': _to_compressed_json({'transcriptPath': self.__valid_transcript}),
             'Error': 'Failed to parse agentStop hook input: sessionId must be a non-empty UUID string.'},
            {'Name': 'Non-string sessionId', 'Json': _to_compressed_json({'sessionId': [valid_guid], 'transcriptPath': self.__valid_transcript}),
             'Error': 'Failed to parse agentStop hook input: sessionId must be a non-empty UUID string.'},
            {'Name': 'Whitespace sessionId', 'Json': _to_compressed_json({'sessionId': ' \t', 'transcriptPath': self.__valid_transcript}),
             'Error': 'Failed to parse agentStop hook input: sessionId must be a non-empty UUID string.'},
            {'Name': 'Invalid sessionId', 'Json': _to_compressed_json({'sessionId': 'not-a-guid', 'transcriptPath': self.__valid_transcript}),
             'Error': 'Failed to parse agentStop hook input: sessionId must be a valid UUID.'},
            {'Name': 'Case-sensitive sessionId', 'Json': _to_compressed_json({'SessionId': valid_guid, 'transcriptPath': self.__valid_transcript}),
             'Error': 'Failed to parse agentStop hook input: sessionId must be a non-empty UUID string.'},
        ]

        for case in missing_path_cases:
            with self.subTest(case=case):
                exit_code, output, error = self.__invoke_hook_input(case['Json'])
                if exit_code != 2 or output or case['Error'] not in error:
                    raise AssertionError(f"Expected exit 2 for '{case['Name']}', got {exit_code}. Error: {error}")

        non_existent_session_id = _new_session_id()

        no_state_json = _to_compressed_json({
            'sessionId': non_existent_session_id,
            'transcriptPath': self.__valid_transcript,
        })

        exit_code, output, error = self.__invoke_hook_input(no_state_json)

        if exit_code != 0 or output.strip():
            raise AssertionError(
                f'Expected exit 0 and empty output when state file does not exist. Got exit {exit_code}, Output: {output}')


        corrupt_session_id = _new_session_id()

        corrupt_state_path = os.path.join(self.__debug_state_dir, f'{corrupt_session_id}.json')

        _write_all_text(corrupt_state_path, '{corrupt-json')

        corrupt_state_json = _to_compressed_json({
            'sessionId': corrupt_session_id,
            'transcriptPath': self.__valid_transcript,
        })

        exit_code, output, error = self.__invoke_hook_input(corrupt_state_json)

        if exit_code != 2 or output or 'Debug-mode state file is not valid JSON:' not in error:
            raise AssertionError(f'Expected exit 2 for corrupt state file. Got exit {exit_code}, Error: {error}')


        with open(corrupt_state_path, 'wb') as file:
            file.write(b'{"enabled":"\xff"}')

        exit_code, output, error = self.__invoke_hook_input(corrupt_state_json)

        if exit_code != 2 or output or 'Debug-mode state file is not valid UTF-8:' not in error:
            raise AssertionError(f'Expected exit 2 for non-UTF-8 state file. Got exit {exit_code}, Error: {error}')


        non_bool_cases = [
            {'Name': 'String "true"', 'Value': 'true'},
            {'Name': 'String "false"', 'Value': 'false'},
            {'Name': 'Number 1', 'Value': 1},
            {'Name': 'Null', 'Value': None},
        ]

        for case in non_bool_cases:
            with self.subTest(case=case):
                session_id = _new_session_id()
                state_path = os.path.join(self.__debug_state_dir, f'{session_id}.json')
                _write_all_text(state_path, _to_compressed_json({'enabled': case['Value']}))

                input_json = _to_compressed_json({'sessionId': session_id, 'transcriptPath': self.__valid_transcript})
                exit_code, output, error = self.__invoke_hook_input(input_json)
                if exit_code != 0 or output.strip():
                    raise AssertionError(
                        f"Expected exit 0 and empty output for non-bool enabled '{case['Name']}'. "
                        f'Got exit {exit_code}, Output: {output}')

        disabled_session_id = _new_session_id()

        disabled_state_path = os.path.join(self.__debug_state_dir, f'{disabled_session_id}.json')

        _write_all_text(disabled_state_path, _to_compressed_json({'enabled': False}))

        disabled_json = _to_compressed_json({'sessionId': disabled_session_id, 'transcriptPath': self.__valid_transcript})

        exit_code, output, error = self.__invoke_hook_input(disabled_json)

        if exit_code != 0 or output.strip():
            raise AssertionError(f'Expected exit 0 and empty output when enabled is false. Got exit {exit_code}, Output: {output}')


        bad_transcript = os.path.join(self.__temp_dir, 'bad-line.jsonl')

        _write_all_text(bad_transcript, '{bad-json\n')

        bad_transcript_json = _to_compressed_json({'sessionId': self.__enabled_session_id, 'transcriptPath': bad_transcript})

        exit_code, output, error = self.__invoke_hook_input(bad_transcript_json)

        if exit_code != 2:
            raise AssertionError(f'Expected exit 2 for bad transcript line. Got exit {exit_code}, Error: {error}')


        no_assistant_transcript = os.path.join(self.__temp_dir, 'no-assistant.jsonl')

        _write_all_text(no_assistant_transcript, _to_compressed_json({
            'type': 'user.message',
            'data': {'content': 'Hello'},
        }) + '\n')

        no_assistant_json = _to_compressed_json({'sessionId': self.__enabled_session_id, 'transcriptPath': no_assistant_transcript})

        exit_code, output, error = self.__invoke_hook_input(no_assistant_json)

        if exit_code != 2:
            raise AssertionError(f'Expected exit 2 for transcript without assistant.message. Got exit {exit_code}, Error: {error}')


        exit_code, output, error = self.__invoke_hook_input(self.__valid_block_json)

        if exit_code != 0:
            raise AssertionError(f'Expected exit 0 for valid run with missing footer. Got exit {exit_code}, Error: {error}')

        block_output = json.loads(output)

        if block_output.get('decision') != 'block' or not str(block_output.get('reason') or '').strip():
            raise AssertionError(f"Expected decision 'block' with non-empty reason. Got output: {output}")


        delayed_transcript = os.path.join(self.__temp_dir, 'delayed.jsonl')

        _write_all_text(delayed_transcript, _to_compressed_json({
            'type': 'assistant.message', 'data': {'content': ''},
        }) + '\n')

        hook_timestamp = 1792345182897

        def _append_delayed_events():
            time.sleep(0.5)
            with open(delayed_transcript, 'a', encoding='utf-8', newline='') as file:
                file.write(_to_compressed_json({'type': 'assistant.message', 'data': {'content': 'Delayed response'}}) + '\n')
                file.write(_to_compressed_json({
                    'type': 'hook.start',
                    'data': {'hookType': 'agentStop', 'input': {'timestamp': hook_timestamp}},
                }) + '\n')

        writer = threading.Thread(target=_append_delayed_events)

        writer.start()

        exit_code, output, error = self.__invoke_hook_input(_to_compressed_json({
            'sessionId': self.__enabled_session_id, 'transcriptPath': delayed_transcript, 'timestamp': hook_timestamp,
        }))

        writer.join()

        if exit_code != 0 or not output.strip() or json.loads(output).get('decision') != 'block':
            raise AssertionError(f'Expected block after delayed transcript flush. Got exit {exit_code}, Output: {output}, Error: {error}')


        hook_transcript = os.path.join(self.__temp_dir, 'with-hook.jsonl')

        _write_all_text(hook_transcript, '\n'.join([
            _to_compressed_json({'type': 'hook.start', 'data': {'hookType': 'preToolUse'}}),
            _to_compressed_json({'type': 'assistant.message', 'data': {'content': 'Hello world'}}),
        ]) + '\n')

        hook_json = _to_compressed_json({'sessionId': self.__enabled_session_id, 'transcriptPath': hook_transcript})

        exit_code, output, error = self.__invoke_hook_input(hook_json)

        if exit_code != 0 or error:
            raise AssertionError(f'Expected exit 0 for transcript with hook event. Got exit {exit_code}, Error: {error}')

        block_output = json.loads(output)

        if block_output.get('decision') != 'block' or '- block-commands' not in block_output.get('reason', ''):
            raise AssertionError(f'Expected repository hook definitions to identify block-commands. Got output: {output}')


        current_jst = runpy.run_path(
            os.path.join(_utilities_directory, 'get-current-jst', 'get-current-jst.py')
        )['current_jst']

        formatted_footer = runpy.run_path(os.path.join(_script_directory, 'format-response-footer.py'))['format_response_footer'](
            hooks=['none'],
            fired_hooks=['none'],
            skills=['none'],
            current_jst=current_jst,
        )

        valid_with_footer_transcript = os.path.join(self.__temp_dir, 'with-footer.jsonl')

        _write_all_text(valid_with_footer_transcript, _to_compressed_json({
            'type': 'assistant.message',
            'data': {'content': f'Here is the response.\n\n{formatted_footer}'},
        }) + '\n')

        with_footer_json = _to_compressed_json({'sessionId': self.__enabled_session_id, 'transcriptPath': valid_with_footer_transcript})

        exit_code, output, error = self.__invoke_hook_input(with_footer_json)

        if exit_code != 0 or output.strip():
            raise AssertionError(f'Expected exit 0 and empty output when valid footer exists. Got exit {exit_code}, Output: {output}')


        for transcript in (self.__valid_transcript, valid_with_footer_transcript):
            with self.subTest(transcript=transcript):
                for debug_session_id in (self.__enabled_session_id, disabled_session_id):
                    exit_code, output, error = self.__invoke_hook_input(_to_compressed_json({
                        'sessionId': debug_session_id,
                        'transcriptPath': transcript,
                        'stopReason': 'end_turn',
                        'stop_hook_active': False,
                    }))
                    should_block = debug_session_id == self.__enabled_session_id and transcript == self.__valid_transcript
                    if exit_code != 0 or error:
                        raise AssertionError(f'Expected camelCase input to succeed: {exit_code}, {output}, {error}')
                    if should_block:
                        decision = json.loads(output)
                        if set(decision) != {'decision', 'reason'} or decision['decision'] != 'block' or not decision['reason']:
                            raise AssertionError(f'Expected footer correction for camelCase input: {output}')
                    elif output:
                        raise AssertionError(f'Expected no correction for disabled mode or valid footer: {output}')

        for state in ([], [{'enabled': True}], None):
            with self.subTest(state=state):
                _write_all_text(self.__enabled_state_path, _to_compressed_json(state))
                exit_code, output, error = self.__invoke_hook_input(self.__valid_block_json)
                if exit_code != 2 or output or 'Debug-mode state is invalid: Expected a JSON object.' not in error:
                    raise AssertionError(f'Expected explicit error for non-object state: {state!r}, {exit_code}, {error}')

        _write_all_text(self.__enabled_state_path, _to_compressed_json({'enabled': True}))

        for event, expected_error in (
            ([], 'line 1'),
            ([{'type': 'assistant.message'}], 'line 1'),
            ({'type': 'assistant.message', 'data': []}, 'Event data must be a JSON object.'),
            ({'type': 'assistant.message', 'data': None}, 'Event data must be a JSON object.'),
            # 依頼ごとに分けるget-turn-eventsの規則で、assistant.message以外の行もdataの形を検証する。
            ({'type': 'user.message', 'data': []}, 'Event data must be a JSON object.'),
            ({'type': 'user.message', 'data': {'content': 'request'}}, 'No assistant response was found in the transcript.'),
        ):
            with self.subTest(event=event, expected_error=expected_error):
                _write_all_text(bad_transcript, _to_compressed_json(event) + '\n')
                exit_code, output, error = self.__invoke_hook_input(bad_transcript_json)
                if exit_code != 2 or output or expected_error not in error:
                    raise AssertionError(f'Expected explicit event shape error: {event!r}, {exit_code}, {error}')

        unicode_transcript = os.path.join(self.__temp_dir, 'unicode.jsonl')

        _write_all_text(unicode_transcript, '\ufeff' + _to_compressed_json({
            'type': 'assistant.message', 'data': {'content': '\u65e5\u672c\u8a9e\u0085\u2028\u2029'},
        }) + '\r\n')

        _write_all_text(self.__enabled_state_path, '\ufeff' + _to_compressed_json({'enabled': True}))

        exit_code, output, error = self.__invoke_hook_input(_to_compressed_json({
            'sessionId': self.__enabled_session_id.upper().replace('-', ''), 'transcriptPath': unicode_transcript,
        }))

        if exit_code != 0 or error or json.loads(output).get('decision') != 'block':
            raise AssertionError(f'Expected UTF-8 and normalized UUID support: {exit_code}, {output}, {error}')


        hooks_directory = os.path.join(self.__temp_dir, 'hooks')

        os.makedirs(hooks_directory)

        for event_name, hook_names in (
            ('agentStop', ('z-last', 'a-first')),
            ('preToolUse', ('a-first', 'z-last')),
        ):
            with self.subTest(event_name=event_name, hook_names=hook_names):
                for hook_name in hook_names:
                    os.makedirs(os.path.join(hooks_directory, 'helpers', 'events', event_name, hook_name))
                _write_all_text(
                    os.path.join(hooks_directory, f'{event_name}.json'),
                    _to_compressed_json({'hooks': {event_name: []}}),
                )

        _write_all_text(
            os.path.join(hooks_directory, 'sessionStart.json'),
            _to_compressed_json({'hooks': {'sessionStart': []}}),
        )

        # このリポジトリ専用のhookは、-repo-<event-name>.jsonで定義し、helpers/-repo-events配下に置く。
        os.makedirs(os.path.join(hooks_directory, 'helpers', '-repo-events', 'preToolUse', 'm-repo'))
        _write_all_text(
            os.path.join(hooks_directory, '-repo-preToolUse.json'),
            _to_compressed_json({'hooks': {'preToolUse': []}}),
        )

        collection_transcript = os.path.join(self.__temp_dir, 'collection.jsonl')

        correction = block_output['reason']

        def _epoch_ms(timestamp):
            return int(datetime.fromisoformat(timestamp).timestamp() * 1000)

        # userPromptSubmittedのhook.startはuser.messageより前に記録されるため、そこからを今回のターンとする。
        record_hook_run = runpy.run_path(
            os.path.join(_utilities_directory, 'hook-activity-state', 'hook-activity-state.py')
        )['record_hook_run']
        with patch.object(tempfile, 'tempdir', self.__temp_dir):
            for timestamp, event_name, hook_name, fired in (
                ('2026-10-21T02:59:58.000Z', 'preToolUse', 'previous-turn', True),
                ('2026-10-21T02:59:59.500Z', 'userPromptSubmitted', 'prompt-hook', True),
                ('2026-10-21T03:00:05.000Z', 'preToolUse', 'deny-hook', True),
                ('2026-10-21T03:00:05.000Z', 'preToolUse', 'quiet-hook', False),
                ('2026-10-21T03:00:06.000Z', 'agentStop', 'stop-hook', True),
            ):
                record_hook_run(self.__enabled_session_id, event_name, hook_name, _epoch_ms(timestamp), fired)

        events = [
            {'type': 'hook.start', 'data': {'hookType': 'sessionStart'}},
            {'type': 'tool.execution_start', 'data': {'toolName': 'skill', 'arguments': {'skill': 'previous'}}},
            {'type': 'hook.start', 'timestamp': '2026-10-21T02:59:59.000Z', 'data': {'hookType': 'userPromptSubmitted'}},
            {'type': 'user.message', 'timestamp': '2026-10-21T03:00:00.000Z', 'data': {'content': 'New turn'}},
            {'type': 'hook.start', 'data': {'hookType': 'agentStop'}},
            {'type': 'tool.execution_start', 'data': {'toolName': 'skill', 'arguments': {'skill': 'first'}}},
            {'type': 'tool.execution_start', 'data': {'toolName': 'functions.skill', 'arguments': {'skill': 'second'}}},
            {'type': 'tool.execution_start', 'data': {'toolName': 'skill', 'arguments': {'skill': 'first'}}},
            {'type': 'tool.execution_start', 'data': {'toolName': 'other', 'arguments': {'skill': 'ignored'}}},
            {'type': 'tool.execution_start', 'data': {'toolName': 'skill', 'arguments': {'skill': ['not-a-string']}}},
            {'type': 'hook.start', 'timestamp': '2026-10-21T03:00:09.000Z', 'data': {'hookType': 'userPromptSubmitted'}},
            {'type': 'user.message', 'timestamp': '2026-10-21T03:00:10.000Z', 'data': {'content': correction}},
            {'type': 'hook.start', 'data': {'hookType': 'preToolUse'}},
        ]

        _write_all_text(collection_transcript, '\n'.join(map(_to_compressed_json, events)) + '\n')

        get_hooks = runpy.run_path(os.path.join(_script_directory, 'get-used-hooks.py'))['get_used_hooks']

        get_skills = runpy.run_path(os.path.join(_script_directory, 'get-used-skills.py'))['get_used_skills']

        if get_hooks(collection_transcript, hooks_directory) != ['a-first', 'm-repo', 'z-last']:
            raise AssertionError('Expected current-turn hooks in name order, retaining correction history.')

        if get_skills(collection_transcript) != ['first', 'second']:
            raise AssertionError('Expected distinct skill names in invocation order, retaining correction history.')

        get_fired_hooks = runpy.run_path(os.path.join(_script_directory, 'get-fired-hooks.py'))['get_fired_hooks']

        with patch.object(tempfile, 'tempdir', self.__temp_dir):
            if get_fired_hooks(collection_transcript, self.__enabled_session_id) != ['deny-hook', 'prompt-hook']:
                raise AssertionError('Expected fired current-turn hooks in name order, excluding agentStop hooks.')


        events.append({'type': 'assistant.message', 'data': {'content': 'Response body'}})

        _write_all_text(collection_transcript, '\n'.join(map(_to_compressed_json, events)) + '\n')

        collection_input = _to_compressed_json({
            'sessionId': self.__enabled_session_id, 'transcriptPath': collection_transcript,
        })

        exit_code, output, error = self.__invoke_hook_input(collection_input)

        if exit_code != 0 or error:
            raise AssertionError(f'Expected correction request for current turn: {exit_code}, {error}')

        decision = json.loads(output)

        if set(decision) != {'decision', 'reason'} or decision['decision'] != 'block':
            raise AssertionError(f'Unexpected hook output shape: {decision}')

        corrected_footer = decision['reason'].split('\n\n', 1)[1]

        if ('- block-commands\n' not in corrected_footer or '- first\n- second\n' not in corrected_footer
                or 'excluding agentStop hooks):\n- deny-hook\n- prompt-hook\n' not in corrected_footer):
            raise AssertionError(f'Expected hooks, fired hooks, and skills in correction request: {corrected_footer}')

        events.extend([
            {'type': 'user.message', 'timestamp': '2026-10-21T03:00:20.000Z', 'data': {'content': decision['reason']}},
            {'type': 'assistant.message', 'data': {'content': 'Response body\n\n' + corrected_footer}},
        ])

        _write_all_text(collection_transcript, '\n'.join(map(_to_compressed_json, events)) + '\n')

        exit_code, output, error = self.__invoke_hook_input(collection_input)

        if exit_code != 0 or output or error:
            raise AssertionError(f'Expected corrected response to pass without another block: {exit_code}, {output}, {error}')


        with open(collection_transcript, 'a', encoding='utf-8', newline='\n') as file:
            file.write(_to_compressed_json({
                'type': 'user.message', 'timestamp': '2026-10-21T03:01:00.000Z', 'data': {'content': 'Next turn'},
            }) + '\n')

        if get_hooks(collection_transcript, hooks_directory) or get_skills(collection_transcript):
            raise AssertionError('Expected a new user turn to reset hooks and skills.')

        with patch.object(tempfile, 'tempdir', self.__temp_dir):
            if get_fired_hooks(collection_transcript, self.__enabled_session_id):
                raise AssertionError('Expected a new user turn to reset fired hooks.')
            for timestamp in (None, 123, 'not-a-time'):
                with self.subTest(timestamp=timestamp):
                    _write_all_text(collection_transcript, _to_compressed_json({
                        'type': 'user.message', 'timestamp': timestamp, 'data': {'content': 'Turn'},
                    }) + '\n')
                    with self.assertRaisesRegex(ValueError, 'Transcript event timestamp'):
                        get_fired_hooks(collection_transcript, self.__enabled_session_id)

    def test_footer_validation(self):
        format_footer = runpy.run_path(os.path.join(_script_directory, 'format-response-footer.py'))['format_response_footer']

        validate_footer = runpy.run_path(os.path.join(_script_directory, 'check-response-footer.py'))['test_response_footer']

        current_jst = '2026-10-21 12:00:00 JST'

        for timestamp, expected in (
            ('2026-10-21 11:50:00 JST', True),
            ('2026-10-21 11:49:59 JST', False),
            ('2026-10-21 12:02:00 JST', True),
            ('2026-10-21 12:02:01 JST', False),
            ('2026-02-30 12:00:00 JST', False),
        ):
            with self.subTest(timestamp=timestamp, expected=expected):
                footer = format_footer(['a', 'b'], ['fired'], ['one'], timestamp)
                if '\r' in footer or not footer.endswith('\n\n'):
                    raise AssertionError('Expected LF-only footer with a trailing blank line.')
                for newline in ('\n', '\r\n'):
                    actual = validate_footer(footer.replace('\n', newline), ['B', 'A'], ['FIRED'], ['ONE'], current_jst)
                    if actual != expected:
                        raise AssertionError(f'Unexpected timestamp validation: {timestamp}, {newline!r}, {actual}')

        footer = format_footer(['a', 'a'], ['fired'], ['one'], current_jst)

        if validate_footer(footer, ['a', 'b'], ['fired'], ['one'], current_jst):
            raise AssertionError('Expected duplicate/missing hooks to invalidate the footer.')

        if validate_footer(footer + 'extra', ['a', 'a'], ['fired'], ['one'], current_jst):
            raise AssertionError('Expected footer to be at the end of the response.')

        if validate_footer(footer, ['a', 'a'], ['fired'], ['different'], current_jst):
            raise AssertionError('Expected mismatching skills to invalidate the footer.')

        if validate_footer(footer, ['a', 'a'], ['other'], ['one'], current_jst):
            raise AssertionError('Expected mismatching fired hooks to invalidate the footer.')

        if '## Hooks fired (recorded by hook runner, excluding agentStop hooks):\n- fired\n' not in footer:
            raise AssertionError('Expected the fired hooks section to state that agentStop hooks are excluded.')

    def test_transcript_and_polling_helpers(self):
        hook = runpy.run_path(os.path.join(_script_directory, 'debug-add-response-footer.py'))

        last_message_transcript = os.path.join(self.__temp_dir, 'last-message.jsonl')

        for content in (None, [], {}, 123, True, '', ' \t\r\n'):
            with self.subTest(content=content):
                events = [
                    {'type': 'assistant.message', 'data': {'content': 'Earlier response'}},
                    {'type': 'assistant.message', 'data': {'content': '  Latest response  '}},
                    {'type': 'assistant.message', 'data': {'content': content}},
                    {'type': 'user.message', 'data': {'content': 'Not an assistant response'}},
                ]
                _write_all_text(last_message_transcript, '\n'.join(map(_to_compressed_json, events)) + '\n')
                if hook['_get_last_assistant_msg'](last_message_transcript) != '  Latest response  ':
                    raise AssertionError(f'Expected the last non-empty assistant response, preserving whitespace: {content!r}')

        def _run_main(input_json, run_path_mock=None):
            with (
                io.TextIOWrapper(io.BytesIO(input_json.encode('utf-8')), encoding='utf-8', newline='\n') as stdin,
                io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='') as stdout,
                io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='') as stderr,
                patch.object(sys, 'stdin', stdin),
                patch.object(sys, 'stdout', stdout),
                patch.object(sys, 'stderr', stderr),
            ):
                with patch.object(tempfile, 'tempdir', self.__temp_dir):
                    if run_path_mock is None:
                        main_exit_code = hook['main']()
                    else:
                        with patch.object(hook['runpy'], 'run_path', run_path_mock):
                            main_exit_code = hook['main']()
                stdout.flush()
                stderr.flush()
                return main_exit_code, stdout.buffer.getvalue().decode('utf-8'), stderr.buffer.getvalue().decode('utf-8')

        def _timestamped_input(timestamp):
            return _to_compressed_json({
                'sessionId': self.__enabled_session_id, 'transcriptPath': self.__valid_transcript, 'timestamp': timestamp,
            })

        real_run_path = runpy.run_path
        wait_mock = Mock()

        def _run_path_with_wait_mock(path, *args, **kwargs):
            if str(path).endswith('wait-for-agent-stop-event.py'):
                return {'wait_for_agent_stop_event': wait_mock}
            return real_run_path(path, *args, **kwargs)

        exit_code, output, error = _run_main(_timestamped_input(123), Mock(side_effect=_run_path_with_wait_mock))
        if exit_code != 0 or error or json.loads(output).get('decision') != 'block':
            raise AssertionError(f'Expected footer check after waiting for hook.start: {exit_code}, {output}, {error}')
        wait_mock.assert_called_once_with(self.__valid_transcript, 123)

        for run_path_mock, expected_error in (
            (Mock(side_effect=OSError('helper missing')), 'Failed to load debug-mode helper: OSError'),
            (Mock(side_effect=SyntaxError('broken helper')), 'Failed to load debug-mode helper: SyntaxError'),
            (Mock(return_value={}), "Failed to load debug-mode helper: KeyError('get_debug_mode')"),
            (Mock(return_value={'get_debug_mode': Mock(side_effect=PermissionError('denied'))}),
             'Failed to read debug-mode state file: denied'),
        ):
            with self.subTest(run_path_mock=run_path_mock, expected_error=expected_error):
                exit_code, output, error = _run_main(self.__valid_block_json, run_path_mock)
                if exit_code != 2 or output or expected_error not in error:
                    raise AssertionError(f'Unexpected debug-mode error handling: {expected_error!r}, {exit_code}, {output}, {error}')


if __name__ == '__main__':
    _run_tests()
