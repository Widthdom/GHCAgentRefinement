import io
import json
import os
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read-hook-input'),
    os.path.join(_utilities_directory, 'get-state-path'),
    os.path.join(_utilities_directory, 'read-transcript-events'),
]
import read_hook_input as _hook_input
from get_state_path import parse_session_id as _parse_session_id
import read_transcript_events as _transcript_events


def main():
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    try:
        hook_input = _hook_input.read_hook_input()
        parsed_session_id = _parse_session_id(hook_input.get('sessionId'))
        if hook_input.get('agentId'):
            return 0

        script_directory = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(_utilities_directory, 'debug-mode-state'))
        from debug_mode_state import get_debug_mode
        if not get_debug_mode(parsed_session_id):
            return 0

        transcript_path = _hook_input.get_transcript_path(hook_input)
        # トランスクリプトは非同期で追記されるため、自身のhook.startが書き込まれるまで最大5秒待つ。
        _transcript_events.wait_for_agent_stop_event(transcript_path, hook_input.get('timestamp'))
        sys.path.insert(0, script_directory)
        from get_prompt_log_paths import get_prompt_log_paths
        paths = get_prompt_log_paths(transcript_path, parsed_session_id)
    # 補助モジュールの読み込みの失敗は、ImportError・SyntaxErrorになる。
    except (OSError, UnicodeError, ValueError, ImportError, SyntaxError, KeyError) as ex:
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
