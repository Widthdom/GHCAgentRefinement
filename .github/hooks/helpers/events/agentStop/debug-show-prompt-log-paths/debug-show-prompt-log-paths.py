import io
import json
import os
import runpy
import sys
import time
import uuid
from typing import cast


def wait_for_stop(transcript_path, session_id, timestamp):
    # トランスクリプトの非同期書き込みを待つ。別のhookの待機処理には依存しない。
    # 単体で入力を渡す場合など、timestampがないときは待たずに読み取る。
    if type(timestamp) is not int:
        return
    deadline = time.monotonic() + 5
    while True:
        with open(transcript_path, encoding='utf-8-sig') as file:
            for line in file:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # 追記中の不完全な行は次の読み取りで再確認する。
                if not isinstance(event, dict) or event.get('type') != 'hook.start':
                    continue
                data = event.get('data')
                if not isinstance(data, dict) or data.get('hookType') != 'agentStop':
                    continue
                hook_input = data.get('input')
                if (isinstance(hook_input, dict) and not hook_input.get('agentId')
                        and hook_input.get('timestamp') == timestamp
                        and hook_input.get('sessionId') == str(session_id)):
                    return
        if time.monotonic() >= deadline:
            raise TimeoutError('Current agentStop event was not recorded within 5 seconds.')
        time.sleep(0.05)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')
    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        session_id = hook_input.get('sessionId')
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('sessionId must be a non-empty UUID string.')
        try:
            parsed_session_id = uuid.UUID(session_id.strip())
        except ValueError:
            raise ValueError('sessionId must be a valid UUID.') from None
        if hook_input.get('agentId'):
            return 0

        script_directory = os.path.dirname(os.path.abspath(__file__))
        helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
        get_debug_mode = runpy.run_path(
            os.path.join(helpers_directory, 'utilities', 'get-debug-mode.py'),
        )['get_debug_mode']
        if not get_debug_mode(parsed_session_id):
            return 0

        transcript_path = hook_input.get('transcriptPath')
        if not isinstance(transcript_path, str) or not os.path.isabs(transcript_path):
            raise ValueError('transcriptPath must be an absolute path string.')
        wait_for_stop(transcript_path, parsed_session_id, hook_input.get('timestamp'))
        get_prompt_log_paths = runpy.run_path(
            os.path.join(script_directory, 'get-prompt-log-paths.py'),
        )['get_prompt_log_paths']
        paths = get_prompt_log_paths(transcript_path, parsed_session_id)
    except (OSError, UnicodeError, ValueError, SyntaxError, KeyError) as ex:
        print(f'Failed to show prompt log paths: {ex}', file=sys.stderr)
        return 2

    if paths:
        # 同じ依頼が別のhookによって継続された場合も、各応答の後で再表示する。
        # 生の文字列ではなく、SDKが画面表示用として処理するprogressを標準出力する。
        for path in paths:
            print(json.dumps({'type': 'progress', 'message': f'Prompt transformation log: {path}'}, ensure_ascii=False))
        print('{}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
