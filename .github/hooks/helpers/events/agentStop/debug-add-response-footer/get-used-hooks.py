import io
import json
import os
import runpy
import sys
from pathlib import Path
from typing import cast


def get_used_hooks(transcript_path, hooks_path):
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f'Transcript file not found: {transcript_path}')

    if not os.path.isdir(hooks_path):
        raise FileNotFoundError(f'Hooks directory not found: {hooks_path}')

    hook_types = set()

    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        content = data.get('content')
        if (event.get('type') == 'user.message' and
                not (isinstance(content, str) and
                     content.startswith('The final response footer is missing or incorrect.'))):
            hook_types.clear()
            continue

        hook_type = data.get('hookType')
        if event.get('type') == 'hook.start' and isinstance(hook_type, str) and hook_type.strip():
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


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--transcript-path', required=True)
    parser.add_argument('--hooks-path', required=True)
    arguments = parser.parse_args()
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8')
    for used_hook in get_used_hooks(arguments.transcript_path, arguments.hooks_path):
        print(used_hook)
