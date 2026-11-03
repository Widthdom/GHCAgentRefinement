import io
import json
import os
import re
import runpy
import sys
from typing import cast


# hookの変更に触れたとみなす語。hookの名前（フォルダー名）は別にリポジトリから集める。
HOOK_REQUEST_PATTERN = re.compile(
    r'hooks?|フック|\.github[/\\]hooks|pre-?tool-?use|post-?tool-?use|agent-?stop|session-?start'
    r'|user-?prompt-?(?:submitted|transformed)',
    re.IGNORECASE,
)
EVENTS_DIRECTORY_NAMES = ('events', '-repo-events')


def find_hooks_directory(cwd):
    # cwdから親へたどり、最初に見つかった `.github/hooks` を返す。なければNone。
    current = os.path.abspath(cwd)
    while True:
        candidate = os.path.join(current, '.github', 'hooks')
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def get_hook_names(cwd):
    # `helpers/events/<event>/<hook>/`・`helpers/-repo-events/<event>/<hook>/` のhook名を集める。
    hooks_directory = find_hooks_directory(cwd)
    if hooks_directory is None:
        return set()
    names = set()
    for events_directory_name in EVENTS_DIRECTORY_NAMES:
        events_directory = os.path.join(hooks_directory, 'helpers', events_directory_name)
        if not os.path.isdir(events_directory):
            continue
        for event_name in os.listdir(events_directory):
            event_directory = os.path.join(events_directory, event_name)
            if os.path.isdir(event_directory):
                names.update(
                    name for name in os.listdir(event_directory) if os.path.isdir(os.path.join(event_directory, name))
                )
    return names


def mentions_hooks(prompt, hook_names):
    if HOOK_REQUEST_PATTERN.search(prompt):
        return True
    lowered = prompt.lower()
    return any(name.lower() in lowered for name in hook_names)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    try:
        # 拒否側のpreToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'hook-change-request-state', 'hook-change-request-state.py',
        ))

        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        prompt = hook_input.get('prompt')
        if not isinstance(prompt, str):
            raise ValueError('prompt must be a string.')
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')

        # サブエージェントへの依頼は親のエージェントが書いたもので、ユーザーの依頼ではないため記録しない。
        # 記録すると、エージェントがサブエージェントへの依頼でhookの変更を自ら許可できてしまう。
        if hook_input.get('agentId'):
            return 0

        # 一度触れたら、このセッションの間は変更を認めたままにする。
        # 「続けて」「1の順に対応して」など、hookに触れない後続の依頼で作業を止めないため。
        if mentions_hooks(prompt, get_hook_names(cwd)):
            state['save_permitted'](state_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to record hook change request: {ex}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
