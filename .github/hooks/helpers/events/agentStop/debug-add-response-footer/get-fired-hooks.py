import os
import runpy
from datetime import datetime


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)


# agentStop hookは本hookと同時に動き、同じ応答終了時の結果をフッター作成時点では確定できないため、常に除外する。
_EXCLUDED_EVENTS = ('agentStop',)


def _to_epoch_ms(timestamp):
    if not isinstance(timestamp, str):
        raise ValueError('Transcript event timestamp must be a string.')
    try:
        # fromisoformatはPython 3.11以降、末尾のZをUTCとして解釈する。
        return int(datetime.fromisoformat(timestamp).timestamp() * 1000)
    except ValueError:
        raise ValueError(f'Transcript event timestamp is invalid: {timestamp}') from None


def _get_turn_started_at_ms(transcript_path):
    # ユーザーの最新の依頼のターンが始まった時刻を返す。依頼のuser.messageがなければ0を返す。
    # 依頼の区切りはget-turn-events utilityと同じく、`delivery` が `queued` でないuser.messageとする。
    # hookの差し戻し（フッター修正の依頼を含む）は `queued` として記録されるため、区切りとしない。
    # userPromptSubmitted・userPromptTransformedのhookはuser.messageより前に記録されるため、その最初のhook.startをターンの開始とする。
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f'Transcript file not found: {transcript_path}')

    turn_started_at_ms = 0
    prompt_hook_started_at = None
    for event in runpy.run_path(
        os.path.join(_utilities_directory, 'read-jsonl-objects', 'read-jsonl-objects.py')
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
        if data.get('delivery') != 'queued':
            turn_started_at_ms = _to_epoch_ms(
                event.get('timestamp') if prompt_hook_started_at is None else prompt_hook_started_at
            )
        prompt_hook_started_at = None
    return turn_started_at_ms


def get_fired_hooks(transcript_path, session_id):
    return runpy.run_path(
        os.path.join(_utilities_directory, 'hook-activity-state', 'hook-activity-state.py')
    )['get_fired_hooks'](session_id, _get_turn_started_at_ms(transcript_path), _EXCLUDED_EVENTS)
