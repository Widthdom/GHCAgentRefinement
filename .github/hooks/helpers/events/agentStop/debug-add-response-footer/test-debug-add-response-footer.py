import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import Mock, patch
from pathlib import Path

script_directory = os.path.dirname(os.path.abspath(__file__))

hook_path = os.path.join(script_directory, 'debug-add-response-footer.py')
temp_dir = os.path.join(tempfile.gettempdir(), str(uuid.uuid4()))
os.makedirs(temp_dir, exist_ok=True)

debug_state_dir = os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement', 'debug-mode')
os.makedirs(debug_state_dir, exist_ok=True)

created_state_files = []


def _invoke_hook_input(input_json):
    completed = subprocess.run(
        [sys.executable, hook_path],
        input=input_json if isinstance(input_json, bytes) else input_json.encode('utf-8'),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.returncode, completed.stdout.decode('utf-8'), completed.stderr.decode('utf-8')


def _to_compressed_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _write_all_text(path, text):
    with open(path, 'w', encoding='utf-8', newline='') as file:
        file.write(text)


def _new_session_id():
    return str(uuid.uuid4())


try:
    tests_passed = 0

    # 1. 入力異常系 (exit 2)
    invalid_inputs = [
        {'Name': 'Empty input', 'Json': '', 'Error': 'agentStop hook input is empty.'},
        {'Name': 'Whitespace input', 'Json': '   \t\n', 'Error': 'agentStop hook input is empty.'},
        {'Name': 'Invalid JSON', 'Json': '{invalid-json}', 'Error': 'Failed to parse agentStop hook input:'},
        {'Name': 'Invalid UTF-8', 'Json': b'{"sessionId":"\xff"}', 'Error': 'Failed to parse agentStop hook input:'},
        {'Name': 'Array JSON', 'Json': '[]', 'Error': 'agentStop hook input must be a JSON object.'},
        {'Name': 'String JSON', 'Json': '"not-an-object"', 'Error': 'agentStop hook input must be a JSON object.'},
        {'Name': 'Null JSON', 'Json': 'null', 'Error': 'agentStop hook input must be a JSON object.'},
        {'Name': 'Boolean JSON', 'Json': 'true', 'Error': 'agentStop hook input must be a JSON object.'},
        {'Name': 'Single object array', 'Json': '[{}]', 'Error': 'agentStop hook input must be a JSON object.'},
    ]

    for case in invalid_inputs:
        exit_code, output, error = _invoke_hook_input(case['Json'])
        if exit_code != 2 or output or case['Error'] not in error:
            raise AssertionError(f"Expected exit 2 for '{case['Name']}', got {exit_code}. Output: {output}, Error: {error}")
        tests_passed += 1

    # 2. transcriptPath と sessionId の異常系 (exit 2)
    valid_guid = _new_session_id()
    valid_transcript = os.path.join(temp_dir, 'valid-transcript.jsonl')
    _write_all_text(valid_transcript, _to_compressed_json({
        'type': 'assistant.message',
        'data': {'content': 'Hello world'},
    }) + '\n')

    missing_path_cases = [
        {'Name': 'Missing transcriptPath', 'Json': _to_compressed_json({'sessionId': valid_guid}),
         'Error': 'agentStop hook transcriptPath must be a string.'},
        {'Name': 'Whitespace transcriptPath', 'Json': _to_compressed_json({'sessionId': valid_guid, 'transcriptPath': '   '}),
         'Error': 'agentStop hook transcriptPath must not be empty or whitespace.'},
        {'Name': 'Nonexistent transcriptPath', 'Json': _to_compressed_json(
            {'sessionId': valid_guid, 'transcriptPath': os.path.join(temp_dir, 'not-found.jsonl')}),
         'Error': 'Transcript path is not a file'},
        {'Name': 'Directory transcriptPath', 'Json': _to_compressed_json({'sessionId': valid_guid, 'transcriptPath': temp_dir}),
         'Error': 'Transcript path is not a file'},
        {'Name': 'Non-string snake_case transcript_path', 'Json': _to_compressed_json(
            {'sessionId': valid_guid, 'transcript_path': []}),
         'Error': 'agentStop hook transcriptPath must be a string.'},
        {'Name': 'Non-string transcriptPath', 'Json': _to_compressed_json({'sessionId': valid_guid, 'transcriptPath': []}),
         'Error': 'agentStop hook transcriptPath must be a string.'},
        {'Name': 'Whitespace transcriptPath', 'Json': _to_compressed_json({'sessionId': valid_guid, 'transcriptPath': '   '}),
         'Error': 'agentStop hook transcriptPath must not be empty or whitespace.'},
        {'Name': 'Invalid transcriptPath with valid snake_case path', 'Json': _to_compressed_json(
            {'sessionId': valid_guid, 'transcriptPath': None, 'transcript_path': valid_transcript}),
         'Error': 'agentStop hook transcriptPath must be a string.'},
        {'Name': 'Missing sessionId', 'Json': _to_compressed_json({'transcriptPath': valid_transcript}),
         'Error': 'agentStop hook sessionId must be a string.'},
        {'Name': 'Non-string sessionId', 'Json': _to_compressed_json({'sessionId': [valid_guid], 'transcriptPath': valid_transcript}),
         'Error': 'agentStop hook sessionId must be a string.'},
        {'Name': 'Whitespace sessionId', 'Json': _to_compressed_json({'sessionId': ' \t', 'transcriptPath': valid_transcript}),
         'Error': 'agentStop hook sessionId must not be empty or whitespace.'},
        {'Name': 'Invalid sessionId', 'Json': _to_compressed_json({'sessionId': 'not-a-guid', 'transcriptPath': valid_transcript}),
         'Error': 'agentStop hook sessionId must be a valid UUID.'},
        {'Name': 'Case-sensitive sessionId', 'Json': _to_compressed_json({'SessionId': valid_guid, 'transcriptPath': valid_transcript}),
         'Error': 'agentStop hook sessionId must be a string.'},
    ]

    for case in missing_path_cases:
        exit_code, output, error = _invoke_hook_input(case['Json'])
        if exit_code != 2 or output or case['Error'] not in error:
            raise AssertionError(f"Expected exit 2 for '{case['Name']}', got {exit_code}. Error: {error}")
        tests_passed += 1

    # 3. デバッグ状態ファイルが存在しない場合 (exit 0, 出力なし)
    non_existent_session_id = _new_session_id()
    no_state_json = _to_compressed_json({
        'sessionId': non_existent_session_id,
        'transcriptPath': valid_transcript,
    })
    exit_code, output, error = _invoke_hook_input(no_state_json)
    if exit_code != 0 or output.strip():
        raise AssertionError(
            f'Expected exit 0 and empty output when state file does not exist. Got exit {exit_code}, Output: {output}')
    tests_passed += 1

    # 4. デバッグ状態ファイルのパース失敗 (exit 2)
    corrupt_session_id = _new_session_id()
    corrupt_state_path = os.path.join(debug_state_dir, f'{corrupt_session_id}.json')
    _write_all_text(corrupt_state_path, '{corrupt-json')
    created_state_files.append(corrupt_state_path)

    corrupt_state_json = _to_compressed_json({
        'sessionId': corrupt_session_id,
        'transcriptPath': valid_transcript,
    })
    exit_code, output, error = _invoke_hook_input(corrupt_state_json)
    if exit_code != 2 or output or 'Debug-mode state file is not valid JSON:' not in error:
        raise AssertionError(f'Expected exit 2 for corrupt state file. Got exit {exit_code}, Error: {error}')
    tests_passed += 1

    # 4-2. デバッグ状態ファイルがUTF-8として復号できない (exit 2)
    with open(corrupt_state_path, 'wb') as file:
        file.write(b'{"enabled":"\xff"}')
    exit_code, output, error = _invoke_hook_input(corrupt_state_json)
    if exit_code != 2 or output or 'Debug-mode state file is not valid UTF-8:' not in error:
        raise AssertionError(f'Expected exit 2 for non-UTF-8 state file. Got exit {exit_code}, Error: {error}')
    tests_passed += 1

    # 5. enabled が bool 以外 (exit 0, 出力なし - 安全側に倒す)
    non_bool_cases = [
        {'Name': 'String "true"', 'Value': 'true'},
        {'Name': 'String "false"', 'Value': 'false'},
        {'Name': 'Number 1', 'Value': 1},
        {'Name': 'Null', 'Value': None},
    ]
    for case in non_bool_cases:
        session_id = _new_session_id()
        state_path = os.path.join(debug_state_dir, f'{session_id}.json')
        _write_all_text(state_path, _to_compressed_json({'enabled': case['Value']}))
        created_state_files.append(state_path)

        input_json = _to_compressed_json({'sessionId': session_id, 'transcriptPath': valid_transcript})
        exit_code, output, error = _invoke_hook_input(input_json)
        if exit_code != 0 or output.strip():
            raise AssertionError(
                f"Expected exit 0 and empty output for non-bool enabled '{case['Name']}'. "
                f'Got exit {exit_code}, Output: {output}')
        tests_passed += 1

    # 6. enabled が false (exit 0, 出力なし)
    disabled_session_id = _new_session_id()
    disabled_state_path = os.path.join(debug_state_dir, f'{disabled_session_id}.json')
    _write_all_text(disabled_state_path, _to_compressed_json({'enabled': False}))
    created_state_files.append(disabled_state_path)

    disabled_json = _to_compressed_json({'sessionId': disabled_session_id, 'transcriptPath': valid_transcript})
    exit_code, output, error = _invoke_hook_input(disabled_json)
    if exit_code != 0 or output.strip():
        raise AssertionError(f'Expected exit 0 and empty output when enabled is false. Got exit {exit_code}, Output: {output}')
    tests_passed += 1

    # 7. トランスクリプト内容の異常 (exit 2)
    # 7-1. JSON パースエラー行
    bad_transcript = os.path.join(temp_dir, 'bad-line.jsonl')
    _write_all_text(bad_transcript, '{bad-json\n')
    enabled_session_id = _new_session_id()
    enabled_state_path = os.path.join(debug_state_dir, f'{enabled_session_id}.json')
    _write_all_text(enabled_state_path, _to_compressed_json({'enabled': True}))
    created_state_files.append(enabled_state_path)

    bad_transcript_json = _to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': bad_transcript})
    exit_code, output, error = _invoke_hook_input(bad_transcript_json)
    if exit_code != 2:
        raise AssertionError(f'Expected exit 2 for bad transcript line. Got exit {exit_code}, Error: {error}')
    tests_passed += 1

    # 7-2. assistant.message が存在しない
    no_assistant_transcript = os.path.join(temp_dir, 'no-assistant.jsonl')
    _write_all_text(no_assistant_transcript, _to_compressed_json({
        'type': 'user.message',
        'data': {'content': 'Hello'},
    }) + '\n')

    no_assistant_json = _to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': no_assistant_transcript})
    exit_code, output, error = _invoke_hook_input(no_assistant_json)
    if exit_code != 2:
        raise AssertionError(f'Expected exit 2 for transcript without assistant.message. Got exit {exit_code}, Error: {error}')
    tests_passed += 1

    # 8. デバッグモード有効時: フッターがない場合にブロック判定を返す (exit 0, decision: block)
    valid_block_json = _to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': valid_transcript})
    exit_code, output, error = _invoke_hook_input(valid_block_json)
    if exit_code != 0:
        raise AssertionError(f'Expected exit 0 for valid run with missing footer. Got exit {exit_code}, Error: {error}')
    block_output = json.loads(output)
    if block_output.get('decision') != 'block' or not str(block_output.get('reason') or '').strip():
        raise AssertionError(f"Expected decision 'block' with non-empty reason. Got output: {output}")
    tests_passed += 1

    # 8-2. 最終応答と本hookのhook.startが遅れて書き込まれる場合は、書き込みを待ってから判定する
    delayed_transcript = os.path.join(temp_dir, 'delayed.jsonl')
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
    exit_code, output, error = _invoke_hook_input(_to_compressed_json({
        'sessionId': enabled_session_id, 'transcriptPath': delayed_transcript, 'timestamp': hook_timestamp,
    }))
    writer.join()
    if exit_code != 0 or not output.strip() or json.loads(output).get('decision') != 'block':
        raise AssertionError(f'Expected block after delayed transcript flush. Got exit {exit_code}, Output: {output}, Error: {error}')
    tests_passed += 1

    hook_transcript = os.path.join(temp_dir, 'with-hook.jsonl')
    _write_all_text(hook_transcript, '\n'.join([
        _to_compressed_json({'type': 'hook.start', 'data': {'hookType': 'preToolUse'}}),
        _to_compressed_json({'type': 'assistant.message', 'data': {'content': 'Hello world'}}),
    ]) + '\n')
    hook_json = _to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': hook_transcript})
    exit_code, output, error = _invoke_hook_input(hook_json)
    if exit_code != 0 or error:
        raise AssertionError(f'Expected exit 0 for transcript with hook event. Got exit {exit_code}, Error: {error}')
    block_output = json.loads(output)
    if block_output.get('decision') != 'block' or '- block-commands' not in block_output.get('reason', ''):
        raise AssertionError(f'Expected repository hook definitions to identify block-commands. Got output: {output}')
    tests_passed += 1

    # 9. デバッグモード有効時: 正しいフッターが既に存在する場合は何もしない (exit 0, 出力なし)
    current_jst = runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'get-current-jst.py')
    )['current_jst']
    formatted_footer = runpy.run_path(str(Path(script_directory) / 'format-response-footer.py'))['format_response_footer'](
        hooks=['none'],
        skills=['none'],
        current_jst=current_jst,
    )

    valid_with_footer_transcript = os.path.join(temp_dir, 'with-footer.jsonl')
    _write_all_text(valid_with_footer_transcript, _to_compressed_json({
        'type': 'assistant.message',
        'data': {'content': f'Here is the response.\n\n{formatted_footer}'},
    }) + '\n')

    with_footer_json = _to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': valid_with_footer_transcript})
    exit_code, output, error = _invoke_hook_input(with_footer_json)
    if exit_code != 0 or output.strip():
        raise AssertionError(f'Expected exit 0 and empty output when valid footer exists. Got exit {exit_code}, Output: {output}')
    tests_passed += 1

    for transcript in (valid_transcript, valid_with_footer_transcript):
        for debug_session_id in (enabled_session_id, disabled_session_id):
            exit_code, output, error = _invoke_hook_input(_to_compressed_json({
                'sessionId': debug_session_id,
                'transcriptPath': transcript,
                'stopReason': 'end_turn',
                'stop_hook_active': False,
            }))
            should_block = debug_session_id == enabled_session_id and transcript == valid_transcript
            if exit_code != 0 or error:
                raise AssertionError(f'Expected camelCase input to succeed: {exit_code}, {output}, {error}')
            if should_block:
                decision = json.loads(output)
                if set(decision) != {'decision', 'reason'} or decision['decision'] != 'block' or not decision['reason']:
                    raise AssertionError(f'Expected footer correction for camelCase input: {output}')
            elif output:
                raise AssertionError(f'Expected no correction for disabled mode or valid footer: {output}')
            tests_passed += 1

    # Python標準のJSON処理では配列を展開せず、文字列以外の値を暗黙変換しない。
    for state in ([], [{'enabled': True}], None):
        _write_all_text(enabled_state_path, _to_compressed_json(state))
        exit_code, output, error = _invoke_hook_input(valid_block_json)
        if exit_code != 2 or output or 'Debug-mode state is invalid: Expected a JSON object.' not in error:
            raise AssertionError(f'Expected explicit error for non-object state: {state!r}, {exit_code}, {error}')
        tests_passed += 1
    _write_all_text(enabled_state_path, _to_compressed_json({'enabled': True}))

    for event, expected_error in (
        ([], 'line 1'),
        ([{'type': 'assistant.message'}], 'line 1'),
        ({'type': 'assistant.message', 'data': []}, 'Event data must be a JSON object.'),
        ({'type': 'assistant.message', 'data': None}, 'Event data must be a JSON object.'),
        # assistant.message以外の行はdataの形を問わず読み飛ばす。
        ({'type': 'user.message', 'data': []}, 'No assistant response was found in the transcript.'),
    ):
        _write_all_text(bad_transcript, _to_compressed_json(event) + '\n')
        exit_code, output, error = _invoke_hook_input(bad_transcript_json)
        if exit_code != 2 or output or expected_error not in error:
            raise AssertionError(f'Expected explicit event shape error: {event!r}, {exit_code}, {error}')
        tests_passed += 1

    # UTF-8 BOM、CRLF、日本語とUnicodeの行区切り文字を含むJSON文字列を保持する。
    unicode_transcript = os.path.join(temp_dir, 'unicode.jsonl')
    _write_all_text(unicode_transcript, '\ufeff' + _to_compressed_json({
        'type': 'assistant.message', 'data': {'content': '\u65e5\u672c\u8a9e\u0085\u2028\u2029'},
    }) + '\r\n')
    _write_all_text(enabled_state_path, '\ufeff' + _to_compressed_json({'enabled': True}))
    exit_code, output, error = _invoke_hook_input(_to_compressed_json({
        'sessionId': enabled_session_id.upper().replace('-', ''), 'transcriptPath': unicode_transcript,
    }))
    if exit_code != 0 or error or json.loads(output).get('decision') != 'block':
        raise AssertionError(f'Expected UTF-8 and normalized UUID support: {exit_code}, {output}, {error}')
    tests_passed += 1

    hooks_directory = os.path.join(temp_dir, 'hooks')
    os.makedirs(hooks_directory)
    for event_name, hook_names in (
        ('agentStop', ('z-last', 'a-first')),
        ('preToolUse', ('a-first', 'z-last')),
    ):
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

    collection_transcript = os.path.join(temp_dir, 'collection.jsonl')
    correction = block_output['reason']
    events = [
        {'type': 'hook.start', 'data': {'hookType': 'sessionStart'}},
        {'type': 'tool.execution_start', 'data': {'toolName': 'skill', 'arguments': {'skill': 'previous'}}},
        {'type': 'user.message', 'data': {'content': 'New turn'}},
        {'type': 'hook.start', 'data': {'hookType': 'agentStop'}},
        {'type': 'tool.execution_start', 'data': {'toolName': 'skill', 'arguments': {'skill': 'first'}}},
        {'type': 'tool.execution_start', 'data': {'toolName': 'functions.skill', 'arguments': {'skill': 'second'}}},
        {'type': 'tool.execution_start', 'data': {'toolName': 'skill', 'arguments': {'skill': 'first'}}},
        {'type': 'tool.execution_start', 'data': {'toolName': 'other', 'arguments': {'skill': 'ignored'}}},
        {'type': 'tool.execution_start', 'data': {'toolName': 'skill', 'arguments': {'skill': ['not-a-string']}}},
        {'type': 'user.message', 'data': {'content': correction}},
        {'type': 'hook.start', 'data': {'hookType': 'preToolUse'}},
    ]
    _write_all_text(collection_transcript, '\n'.join(map(_to_compressed_json, events)) + '\n')
    get_hooks = runpy.run_path(str(Path(script_directory) / 'get-used-hooks.py'))['get_used_hooks']
    get_skills = runpy.run_path(str(Path(script_directory) / 'get-used-skills.py'))['get_used_skills']
    if get_hooks(collection_transcript, hooks_directory) != ['a-first', 'z-last']:
        raise AssertionError('Expected current-turn hooks in name order, retaining correction history.')
    if get_skills(collection_transcript) != ['first', 'second']:
        raise AssertionError('Expected distinct skill names in invocation order, retaining correction history.')
    tests_passed += 2

    events.append({'type': 'assistant.message', 'data': {'content': 'Response body'}})
    _write_all_text(collection_transcript, '\n'.join(map(_to_compressed_json, events)) + '\n')
    collection_input = _to_compressed_json({
        'sessionId': enabled_session_id, 'transcriptPath': collection_transcript,
    })
    exit_code, output, error = _invoke_hook_input(collection_input)
    if exit_code != 0 or error:
        raise AssertionError(f'Expected correction request for current turn: {exit_code}, {error}')
    decision = json.loads(output)
    if set(decision) != {'decision', 'reason'} or decision['decision'] != 'block':
        raise AssertionError(f'Unexpected hook output shape: {decision}')
    corrected_footer = decision['reason'].split('\n\n', 1)[1]
    if '- block-commands\n' not in corrected_footer or '- first\n- second\n' not in corrected_footer:
        raise AssertionError(f'Expected hooks and skills in correction request: {corrected_footer}')
    events.extend([
        {'type': 'user.message', 'data': {'content': decision['reason']}},
        {'type': 'assistant.message', 'data': {'content': 'Response body\n\n' + corrected_footer}},
    ])
    _write_all_text(collection_transcript, '\n'.join(map(_to_compressed_json, events)) + '\n')
    exit_code, output, error = _invoke_hook_input(collection_input)
    if exit_code != 0 or output or error:
        raise AssertionError(f'Expected corrected response to pass without another block: {exit_code}, {output}, {error}')
    tests_passed += 1

    with open(collection_transcript, 'a', encoding='utf-8') as file:
        file.write(_to_compressed_json({'type': 'user.message', 'data': {'content': 'Next turn'}}) + '\n')
    if get_hooks(collection_transcript, hooks_directory) or get_skills(collection_transcript):
        raise AssertionError('Expected a new user turn to reset hooks and skills.')
    tests_passed += 1

    format_footer = runpy.run_path(str(Path(script_directory) / 'format-response-footer.py'))['format_response_footer']
    validate_footer = runpy.run_path(str(Path(script_directory) / 'test-response-footer.py'))['test_response_footer']
    current_jst = '2026-10-21 12:00:00 JST'
    for timestamp, expected in (
        ('2026-10-21 11:50:00 JST', True),
        ('2026-10-21 11:49:59 JST', False),
        ('2026-10-21 12:02:00 JST', True),
        ('2026-10-21 12:02:01 JST', False),
        ('2026-02-30 12:00:00 JST', False),
    ):
        footer = format_footer(['a', 'b'], ['one'], timestamp)
        if '\r' in footer or not footer.endswith('\n\n'):
            raise AssertionError('Expected LF-only footer with a trailing blank line.')
        for newline in ('\n', '\r\n'):
            actual = validate_footer(footer.replace('\n', newline), ['B', 'A'], ['ONE'], current_jst)
            if actual != expected:
                raise AssertionError(f'Unexpected timestamp validation: {timestamp}, {newline!r}, {actual}')
            tests_passed += 1

    footer = format_footer(['a', 'a'], ['one'], current_jst)
    if validate_footer(footer, ['a', 'b'], ['one'], current_jst):
        raise AssertionError('Expected duplicate/missing hooks to invalidate the footer.')
    if validate_footer(footer + 'extra', ['a', 'a'], ['one'], current_jst):
        raise AssertionError('Expected footer to be at the end of the response.')
    if validate_footer(footer, ['a', 'a'], ['different'], current_jst):
        raise AssertionError('Expected mismatching skills to invalidate the footer.')
    tests_passed += 3

    # 待機期限とtimestamp省略時の挙動は実時間を待たずに検証する。
    hook = runpy.run_path(str(Path(script_directory) / 'debug-add-response-footer.py'))
    last_message_transcript = os.path.join(temp_dir, 'last-message.jsonl')
    for content in (None, [], {}, 123, True, '', ' \t\r\n'):
        events = [
            {'type': 'assistant.message', 'data': {'content': 'Earlier response'}},
            {'type': 'assistant.message', 'data': {'content': '  Latest response  '}},
            {'type': 'assistant.message', 'data': {'content': content}},
            {'type': 'user.message', 'data': {'content': 'Not an assistant response'}},
        ]
        _write_all_text(last_message_transcript, '\n'.join(map(_to_compressed_json, events)) + '\n')
        if hook['_get_last_assistant_msg'](last_message_transcript) != '  Latest response  ':
            raise AssertionError(f'Expected the last non-empty assistant response, preserving whitespace: {content!r}')
        tests_passed += 1

    flush_transcript = os.path.join(temp_dir, 'flush.jsonl')
    start_event = {
        'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': 123}},
    }
    _write_all_text(flush_transcript, '\n' + _to_compressed_json(start_event) + '\n{"type":')
    if not hook['_is_agent_stop_event_recorded'](flush_transcript, 123):
        raise AssertionError('Expected own hook.start to end polling.')
    if hook['_is_agent_stop_event_recorded'](flush_transcript, 456):
        raise AssertionError('Expected other hook timestamps and partial lines not to end polling.')
    tests_passed += 2

    is_event_recorded = hook['_is_agent_stop_event_recorded']
    for events in (
        [{'type': 'hook.start', 'data': {'hookType': 'preToolUse', 'input': {'timestamp': 123}}}],
        [{'type': 'tool.execution_start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': 123}}}],
        [{'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': None}}],
        [{'type': 'hook.start', 'data': {'hookType': 'agentStop'}}],
        [{'type': 'hook.start'}],
        [{'type': 'user.message', 'data': []}],
        [{'data': None}],
    ):
        _write_all_text(flush_transcript, '\n'.join(map(_to_compressed_json, events)) + '\n')
        if is_event_recorded(flush_transcript, 123):
            raise AssertionError(f'Expected non-matching events to be ignored: {events!r}')
        tests_passed += 1

    for line, expected_error in (
        ('[]', 'Transcript event must be a JSON object at line 2.'),
        ('{"type":"hook.start","data":[]}', 'Event data must be a JSON object at line 2.'),
        ('{"type":"hook.start","data":null}', 'Event data must be a JSON object at line 2.'),
    ):
        _write_all_text(flush_transcript, '{"type":"user.message"}\n' + line + '\n')
        try:
            is_event_recorded(flush_transcript, 123)
        except ValueError as ex:
            if str(ex) != expected_error:
                raise AssertionError(f'Unexpected error for {line!r}: {ex}')
        else:
            raise AssertionError(f'Expected ValueError for {line!r}.')
        tests_passed += 1

    def _run_main(input_json, run_path_mock=None):
        with (
            io.TextIOWrapper(io.BytesIO(input_json.encode('utf-8')), encoding='utf-8') as stdin,
            io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='') as stdout,
            io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='') as stderr,
            patch.object(sys, 'stdin', stdin),
            patch.object(sys, 'stdout', stdout),
            patch.object(sys, 'stderr', stderr),
        ):
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
            'sessionId': enabled_session_id, 'transcriptPath': valid_transcript, 'timestamp': timestamp,
        })

    check_start = Mock(return_value=False)
    with patch.dict(hook['main'].__globals__, _is_agent_stop_event_recorded=check_start):
        with patch.object(hook['time'], 'monotonic', side_effect=[0, 0, 5]):
            with patch.object(hook['time'], 'sleep') as sleep:
                exit_code, output, error = _run_main(_timestamped_input(123))
                if exit_code != 0 or error or json.loads(output).get('decision') != 'block':
                    raise AssertionError(f'Expected footer check after polling deadline: {exit_code}, {output}, {error}')
                if check_start.call_count != 2:
                    raise AssertionError('Expected bounded polling until the deadline.')
                sleep.assert_called_once_with(0.05)
        tests_passed += 1
    for timestamp in (None, True, '123'):
        check_start = Mock()
        with patch.dict(hook['main'].__globals__, _is_agent_stop_event_recorded=check_start):
            exit_code, output, error = _run_main(_timestamped_input(timestamp))
            if exit_code != 0 or error or json.loads(output).get('decision') != 'block':
                raise AssertionError(f'Expected footer check without polling: {timestamp!r}, {exit_code}, {output}, {error}')
            check_start.assert_not_called()
        tests_passed += 1

    # ヘルパー読み込み失敗と状態ファイル読み取り失敗は、プロセス内でrunpy.run_pathを差し替えて検証する。
    for run_path_mock, expected_error in (
        (Mock(side_effect=OSError('helper missing')), 'Failed to load debug-mode helper: OSError'),
        (Mock(side_effect=SyntaxError('broken helper')), 'Failed to load debug-mode helper: SyntaxError'),
        (Mock(return_value={}), "Failed to load debug-mode helper: KeyError('get_debug_mode')"),
        (Mock(return_value={'get_debug_mode': Mock(side_effect=PermissionError('denied'))}),
         'Failed to read debug-mode state file: denied'),
    ):
        exit_code, output, error = _run_main(valid_block_json, run_path_mock)
        if exit_code != 2 or output or expected_error not in error:
            raise AssertionError(f'Unexpected debug-mode error handling: {expected_error!r}, {exit_code}, {output}, {error}')
        tests_passed += 1

    print(f'Passed {tests_passed} test-debug-add-response-footer tests.')
finally:
    shutil.rmtree(temp_dir, ignore_errors=True)
    for path in created_state_files:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
