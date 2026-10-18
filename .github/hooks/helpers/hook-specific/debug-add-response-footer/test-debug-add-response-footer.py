import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_directory)

from common import load_helper  # noqa: E402

hook_path = os.path.join(script_directory, 'debug-add-response-footer.py')
temp_dir = os.path.join(tempfile.gettempdir(), str(uuid.uuid4()))
os.makedirs(temp_dir, exist_ok=True)

debug_state_dir = os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement', 'debug-mode')
os.makedirs(debug_state_dir, exist_ok=True)

created_state_files = []


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


def write_all_text(path, text):
    with open(path, 'w', encoding='utf-8', newline='') as file:
        file.write(text)


def new_session_id():
    return str(uuid.uuid4())


try:
    tests_passed = 0

    # 1. 入力異常系 (exit 2)
    invalid_inputs = [
        {'Name': 'Empty input', 'Json': ''},
        {'Name': 'Whitespace input', 'Json': '   \t\n'},
        {'Name': 'Invalid JSON', 'Json': '{invalid-json}'},
        {'Name': 'Array JSON', 'Json': '[]'},
        {'Name': 'String JSON', 'Json': '"not-an-object"'},
    ]

    for case in invalid_inputs:
        exit_code, output, error = invoke_hook_input(case['Json'])
        if exit_code != 2:
            raise AssertionError(f"Expected exit 2 for '{case['Name']}', got {exit_code}. Output: {output}, Error: {error}")
        tests_passed += 1

    # 2. transcriptPath と sessionId の異常系 (exit 2)
    valid_guid = new_session_id()
    valid_transcript = os.path.join(temp_dir, 'valid-transcript.jsonl')
    write_all_text(valid_transcript, to_compressed_json({
        'type': 'assistant.message',
        'data': {'content': 'Hello world'},
    }) + '\n')

    missing_path_cases = [
        {'Name': 'Missing transcriptPath', 'Json': to_compressed_json({'sessionId': valid_guid})},
        {'Name': 'Whitespace transcriptPath', 'Json': to_compressed_json({'sessionId': valid_guid, 'transcriptPath': '   '})},
        {'Name': 'Nonexistent transcriptPath', 'Json': to_compressed_json(
            {'sessionId': valid_guid, 'transcriptPath': os.path.join(temp_dir, 'not-found.jsonl')})},
        {'Name': 'Invalid sessionId', 'Json': to_compressed_json({'sessionId': 'not-a-guid', 'transcriptPath': valid_transcript})},
    ]

    for case in missing_path_cases:
        exit_code, output, error = invoke_hook_input(case['Json'])
        if exit_code != 2:
            raise AssertionError(f"Expected exit 2 for '{case['Name']}', got {exit_code}. Error: {error}")
        tests_passed += 1

    # 3. デバッグ状態ファイルが存在しない場合 (exit 0, 出力なし)
    non_existent_session_id = new_session_id()
    no_state_json = to_compressed_json({
        'sessionId': non_existent_session_id,
        'transcriptPath': valid_transcript,
    })
    exit_code, output, error = invoke_hook_input(no_state_json)
    if exit_code != 0 or output.strip():
        raise AssertionError(
            f'Expected exit 0 and empty output when state file does not exist. Got exit {exit_code}, Output: {output}')
    tests_passed += 1

    # 4. デバッグ状態ファイルのパース失敗 (exit 2)
    corrupt_session_id = new_session_id()
    corrupt_state_path = os.path.join(debug_state_dir, f'{corrupt_session_id}.json')
    write_all_text(corrupt_state_path, '{corrupt-json')
    created_state_files.append(corrupt_state_path)

    corrupt_state_json = to_compressed_json({
        'sessionId': corrupt_session_id,
        'transcriptPath': valid_transcript,
    })
    exit_code, output, error = invoke_hook_input(corrupt_state_json)
    if exit_code != 2:
        raise AssertionError(f'Expected exit 2 for corrupt state file. Got exit {exit_code}, Error: {error}')
    tests_passed += 1

    # 5. enabled が bool 以外 (exit 0, 出力なし - 安全側に倒す)
    non_bool_cases = [
        {'Name': 'String "true"', 'Value': 'true'},
        {'Name': 'String "false"', 'Value': 'false'},
        {'Name': 'Number 1', 'Value': 1},
        {'Name': 'Null', 'Value': None},
    ]
    for case in non_bool_cases:
        session_id = new_session_id()
        state_path = os.path.join(debug_state_dir, f'{session_id}.json')
        write_all_text(state_path, to_compressed_json({'enabled': case['Value']}))
        created_state_files.append(state_path)

        input_json = to_compressed_json({'sessionId': session_id, 'transcriptPath': valid_transcript})
        exit_code, output, error = invoke_hook_input(input_json)
        if exit_code != 0 or output.strip():
            raise AssertionError(
                f"Expected exit 0 and empty output for non-bool enabled '{case['Name']}'. "
                f'Got exit {exit_code}, Output: {output}')
        tests_passed += 1

    # 6. enabled が false (exit 0, 出力なし)
    disabled_session_id = new_session_id()
    disabled_state_path = os.path.join(debug_state_dir, f'{disabled_session_id}.json')
    write_all_text(disabled_state_path, to_compressed_json({'enabled': False}))
    created_state_files.append(disabled_state_path)

    disabled_json = to_compressed_json({'sessionId': disabled_session_id, 'transcriptPath': valid_transcript})
    exit_code, output, error = invoke_hook_input(disabled_json)
    if exit_code != 0 or output.strip():
        raise AssertionError(f'Expected exit 0 and empty output when enabled is false. Got exit {exit_code}, Output: {output}')
    tests_passed += 1

    # 7. トランスクリプト内容の異常 (exit 2)
    # 7-1. JSON パースエラー行
    bad_transcript = os.path.join(temp_dir, 'bad-line.jsonl')
    write_all_text(bad_transcript, '{bad-json\n')
    enabled_session_id = new_session_id()
    enabled_state_path = os.path.join(debug_state_dir, f'{enabled_session_id}.json')
    write_all_text(enabled_state_path, to_compressed_json({'enabled': True}))
    created_state_files.append(enabled_state_path)

    bad_transcript_json = to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': bad_transcript})
    exit_code, output, error = invoke_hook_input(bad_transcript_json)
    if exit_code != 2:
        raise AssertionError(f'Expected exit 2 for bad transcript line. Got exit {exit_code}, Error: {error}')
    tests_passed += 1

    # 7-2. assistant.message が存在しない
    no_assistant_transcript = os.path.join(temp_dir, 'no-assistant.jsonl')
    write_all_text(no_assistant_transcript, to_compressed_json({
        'type': 'user.message',
        'data': {'content': 'Hello'},
    }) + '\n')

    no_assistant_json = to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': no_assistant_transcript})
    exit_code, output, error = invoke_hook_input(no_assistant_json)
    if exit_code != 2:
        raise AssertionError(f'Expected exit 2 for transcript without assistant.message. Got exit {exit_code}, Error: {error}')
    tests_passed += 1

    # 8. デバッグモード有効時: フッターがない場合にブロック判定を返す (exit 0, decision: block)
    valid_block_json = to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': valid_transcript})
    exit_code, output, error = invoke_hook_input(valid_block_json)
    if exit_code != 0:
        raise AssertionError(f'Expected exit 0 for valid run with missing footer. Got exit {exit_code}, Error: {error}')
    block_output = json.loads(output)
    if block_output.get('decision') != 'block' or not str(block_output.get('reason') or '').strip():
        raise AssertionError(f"Expected decision 'block' with non-empty reason. Got output: {output}")
    tests_passed += 1

    # 8-2. 最終応答と本hookのhook.startが遅れて書き込まれる場合は、書き込みを待ってから判定する
    delayed_transcript = os.path.join(temp_dir, 'delayed.jsonl')
    write_all_text(delayed_transcript, to_compressed_json({
        'type': 'assistant.message', 'data': {'content': ''},
    }) + '\n')
    hook_timestamp = 1792345182897

    def append_delayed_events():
        time.sleep(0.5)
        with open(delayed_transcript, 'a', encoding='utf-8', newline='') as file:
            file.write(to_compressed_json({'type': 'assistant.message', 'data': {'content': 'Delayed response'}}) + '\n')
            file.write(to_compressed_json({
                'type': 'hook.start',
                'data': {'hookType': 'agentStop', 'input': {'timestamp': hook_timestamp}},
            }) + '\n')

    writer = threading.Thread(target=append_delayed_events)
    writer.start()
    exit_code, output, error = invoke_hook_input(to_compressed_json({
        'sessionId': enabled_session_id, 'transcriptPath': delayed_transcript, 'timestamp': hook_timestamp,
    }))
    writer.join()
    if exit_code != 0 or not output.strip() or json.loads(output).get('decision') != 'block':
        raise AssertionError(f'Expected block after delayed transcript flush. Got exit {exit_code}, Output: {output}, Error: {error}')
    tests_passed += 1

    hook_transcript = os.path.join(temp_dir, 'with-hook.jsonl')
    write_all_text(hook_transcript, '\n'.join([
        to_compressed_json({'type': 'hook.start', 'data': {'hookType': 'preToolUse'}}),
        to_compressed_json({'type': 'assistant.message', 'data': {'content': 'Hello world'}}),
    ]) + '\n')
    hook_json = to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': hook_transcript})
    exit_code, output, error = invoke_hook_input(hook_json)
    if exit_code != 0 or error:
        raise AssertionError(f'Expected exit 0 for transcript with hook event. Got exit {exit_code}, Error: {error}')
    block_output = json.loads(output)
    if block_output.get('decision') != 'block' or '- block-commands' not in block_output.get('reason', ''):
        raise AssertionError(f'Expected repository hook definitions to identify block-commands. Got output: {output}')
    tests_passed += 1

    # 9. デバッグモード有効時: 正しいフッターが既に存在する場合は何もしない (exit 0, 出力なし)
    current_jst = load_helper('get-current-jst').get_current_jst()
    formatted_footer = load_helper('format-response-footer').format_response_footer(
        hooks=['none'],
        skills=['none'],
        current_jst=current_jst,
    )

    valid_with_footer_transcript = os.path.join(temp_dir, 'with-footer.jsonl')
    write_all_text(valid_with_footer_transcript, to_compressed_json({
        'type': 'assistant.message',
        'data': {'content': f'Here is the response.\n\n{formatted_footer}'},
    }) + '\n')

    with_footer_json = to_compressed_json({'sessionId': enabled_session_id, 'transcriptPath': valid_with_footer_transcript})
    exit_code, output, error = invoke_hook_input(with_footer_json)
    if exit_code != 0 or output.strip():
        raise AssertionError(f'Expected exit 0 and empty output when valid footer exists. Got exit {exit_code}, Output: {output}')
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
