import os
import runpy


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_transcript_events = runpy.run_path(os.path.join(
    _utilities_directory, 'read-transcript-events', 'read-transcript-events.py',
))


# agentStop hookは本hookと同時に動き、同じ応答終了時の結果をフッター作成時点では確定できないため、常に除外する。
_EXCLUDED_EVENTS = ('agentStop',)


def _get_turn_started_at_ms(transcript_path):
    # ユーザーの最新の依頼のターンが始まった時刻を返す。依頼のuser.messageがなければ0を返す。
    # 依頼の区切りはread-transcript-events utilityと同じく、`delivery` が `queued` でないuser.messageとする。
    # hookの差し戻し（フッター修正の依頼を含む）は `queued` として記録されるため、区切りとしない。
    # userPromptSubmitted・userPromptTransformedのhookはuser.messageより前に記録されるため、その最初のhook.startをターンの開始とする。
    turn_started_at_ms = 0
    prompt_hook_start = None
    for event in _transcript_events['read_transcript_events'](transcript_path):
        event_type, data = event.get('type'), event['data']
        if event_type == 'hook.start' and data.get('hookType') == 'userPromptSubmitted' and prompt_hook_start is None:
            prompt_hook_start = event
            continue
        if event_type != 'user.message':
            continue
        if data.get('delivery') != 'queued':
            started_event = event if prompt_hook_start is None else prompt_hook_start
            turn_started_at_ms = _transcript_events['get_event_time_ms'](started_event)
            if turn_started_at_ms is None:
                raise ValueError(f"Transcript event timestamp is invalid: {started_event.get('timestamp')!r}")
        prompt_hook_start = None
    return turn_started_at_ms


def get_fired_hooks(transcript_path, session_id):
    return runpy.run_path(
        os.path.join(_utilities_directory, 'hook-activity-state', 'hook-activity-state.py')
    )['get_fired_hooks'](session_id, _get_turn_started_at_ms(transcript_path), _EXCLUDED_EVENTS)
