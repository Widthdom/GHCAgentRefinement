import io
import os
import runpy
import sys
from datetime import datetime
from pathlib import Path
from typing import cast


FOOTER_CORRECTION_PREFIX = 'The final response footer is missing or incorrect.'
# agentStop hookは本hookと同時に動き、同じ応答終了時の結果をフッター作成時点では確定できないため、常に除外する。
EXCLUDED_EVENTS = ('agentStop',)


def _to_epoch_ms(timestamp):
    if not isinstance(timestamp, str):
        raise ValueError('Transcript event timestamp must be a string.')
    try:
        # fromisoformatはPython 3.11以降、末尾のZをUTCとして解釈する。
        return int(datetime.fromisoformat(timestamp).timestamp() * 1000)
    except ValueError:
        raise ValueError(f'Transcript event timestamp is invalid: {timestamp}') from None


def get_turn_started_at_ms(transcript_path):
    # 直近の（フッター修正依頼でない）user.messageのターンが始まった時刻を返す。user.messageがなければ0を返す。
    # userPromptSubmitted・userPromptTransformedのhookはuser.messageより前に記録されるため、その最初のhook.startをターンの開始とする。
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f'Transcript file not found: {transcript_path}')

    turn_started_at_ms = 0
    prompt_hook_started_at = None
    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        event_type = event.get('type')
        if (event_type == 'hook.start' and data.get('hookType') == 'userPromptSubmitted'
                and prompt_hook_started_at is None):
            prompt_hook_started_at = event.get('timestamp')
            continue
        if event_type != 'user.message':
            continue
        content = data.get('content')
        if not (isinstance(content, str) and content.startswith(FOOTER_CORRECTION_PREFIX)):
            turn_started_at_ms = _to_epoch_ms(
                event.get('timestamp') if prompt_hook_started_at is None else prompt_hook_started_at
            )
        prompt_hook_started_at = None
    return turn_started_at_ms


def get_fired_hooks(transcript_path, session_id):
    return runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'hook-activity-state.py')
    )['get_fired_hooks'](session_id, get_turn_started_at_ms(transcript_path), EXCLUDED_EVENTS)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--transcript-path', required=True)
    parser.add_argument('--session-id', required=True)
    arguments = parser.parse_args()
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8')
    for fired_hook in get_fired_hooks(arguments.transcript_path, arguments.session_id):
        print(fired_hook)
