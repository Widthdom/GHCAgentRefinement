import json
import os
import runpy


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_get_turns = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))['get_turns']


def get_used_hooks(transcript_path, hooks_path):
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f'Transcript file not found: {transcript_path}')

    if not os.path.isdir(hooks_path):
        raise FileNotFoundError(f'Hooks directory not found: {hooks_path}')

    # ユーザーの最新の依頼以降のイベントを対象とする。hookの差し戻し（フッター修正の依頼を含む）は依頼の区切りとしない。
    hook_types = set()
    for event_type, data in _get_turns(transcript_path)[-1]:
        hook_type = data.get('hookType')
        if event_type == 'hook.start' and isinstance(hook_type, str) and hook_type.strip():
            hook_types.add(hook_type)

    used_hooks = set()
    with os.scandir(hooks_path) as entries:
        hook_files = sorted(
            (entry for entry in entries if entry.is_file() and entry.name.endswith('.json')),
            key=lambda entry: entry.name,
        )
    for hook_file in hook_files:
        with open(hook_file.path, encoding='utf-8-sig') as file:
            hook_definition = json.load(file)
        if not isinstance(hook_definition, dict):
            raise ValueError(f'Hook definition must be a JSON object: {hook_file.path}')
        configured_hooks = hook_definition.get('hooks', {})
        if not isinstance(configured_hooks, dict):
            raise ValueError(f'Hook definitions must contain a hooks object: {hook_file.path}')
        # このリポジトリ専用のhookの定義（-repo-<event-name>.json）は helpers/-repo-events 配下のhookを起動する。
        events_name = '-repo-events' if hook_file.name.startswith('-repo-') else 'events'
        for hook_type in hook_types.intersection(configured_hooks):
            event_hooks_directory = os.path.join(hooks_path, 'helpers', events_name, hook_type)
            if not os.path.isdir(event_hooks_directory):
                raise FileNotFoundError(f'Event hooks directory not found: {event_hooks_directory}')
            with os.scandir(event_hooks_directory) as entries:
                used_hooks.update(entry.name for entry in entries if entry.is_dir())

    return sorted(used_hooks)
