import io
import json
import os
import runpy
import sys
import time
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_parse_session_id = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['parse_session_id']


def _wait_for_stop(transcript_path, session_id, timestamp):
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
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    try:
        hook_input = _hook_input['read_hook_input']()
        parsed_session_id = _parse_session_id(hook_input.get('sessionId'))
        if hook_input.get('agentId'):
            return 0

        script_directory = os.path.dirname(os.path.abspath(__file__))
        helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
        get_debug_mode = runpy.run_path(
            os.path.join(helpers_directory, 'utilities', 'get-debug-mode', 'get-debug-mode.py'),
        )['get_debug_mode']
        if not get_debug_mode(parsed_session_id):
            return 0

        transcript_path = hook_input.get('transcriptPath')
        if not isinstance(transcript_path, str) or not os.path.isabs(transcript_path):
            raise ValueError('transcriptPath must be an absolute path string.')
        _wait_for_stop(transcript_path, parsed_session_id, hook_input.get('timestamp'))
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
