import io
import json
import os
import runpy
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read_hook_input.py'))
_parse_session_id = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get_state_path.py'))['parse_session_id']
_transcript_events = runpy.run_path(os.path.join(_utilities_directory, 'read-transcript-events', 'read_transcript_events.py'))


def main():
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    try:
        hook_input = _hook_input['read_hook_input']()
        parsed_session_id = _parse_session_id(hook_input.get('sessionId'))
        if hook_input.get('agentId'):
            return 0

        script_directory = os.path.dirname(os.path.abspath(__file__))
        get_debug_mode = runpy.run_path(
            os.path.join(_utilities_directory, 'debug-mode-state', 'debug_mode_state.py'),
        )['get_debug_mode']
        if not get_debug_mode(parsed_session_id):
            return 0

        transcript_path = _hook_input['get_transcript_path'](hook_input)
        # トランスクリプトは非同期で追記されるため、自身のhook.startが書き込まれるまで最大5秒待つ。
        _transcript_events['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))
        get_prompt_log_paths = runpy.run_path(
            os.path.join(script_directory, 'get_prompt_log_paths.py'),
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
