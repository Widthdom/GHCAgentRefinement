import glob
import os
import runpy
import tempfile
import uuid


def get_prompt_log_paths(transcript_path, session_id):
    session_id = uuid.UUID(str(session_id))
    script_directory = os.path.dirname(os.path.abspath(__file__))
    helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
    read_jsonl_objects = runpy.run_path(
        os.path.join(helpers_directory, 'utilities', 'read-jsonl-objects', 'read-jsonl-objects.py'),
    )['read_jsonl_objects']
    timestamps = set()
    response_started = False
    for event in read_jsonl_objects(transcript_path):
        if event.get('type') != 'hook.start':
            continue
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        hook_input = data.get('input')
        if not isinstance(hook_input, dict) or hook_input.get('agentId'):
            continue
        try:
            event_session = uuid.UUID(str(hook_input.get('sessionId')))
        except ValueError:
            continue
        if event_session != session_id:
            continue

        hook_type = data.get('hookType')
        if hook_type == 'userPromptSubmitted':
            timestamps.clear()
            response_started = False
        elif hook_type == 'agentStop':
            # 応答継続による内部的なプロンプト変換は除外し、
            # ユーザーの依頼に対して最初の応答終了までに作られたログに固定する。
            response_started = True
        elif hook_type == 'userPromptTransformed' and not response_started:
            timestamp = hook_input.get('timestamp')
            if type(timestamp) is int and timestamp >= 0:
                timestamps.add(timestamp)

    log_directory = os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'debug-logs', 'prompt-transformations', str(session_id),
    )
    paths = []
    for timestamp in sorted(timestamps):
        # 現在の依頼で実際に発動したイベントと照合し、過去のログを表示しない。
        paths.extend(sorted(
            path for path in glob.glob(os.path.join(glob.escape(log_directory), f'{timestamp}-*.md'))
            if os.path.isfile(path)
        ))
    return paths
