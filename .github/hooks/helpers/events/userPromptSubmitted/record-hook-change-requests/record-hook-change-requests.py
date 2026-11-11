import io
import os
import re
import runpy
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))


# hookの変更に触れたとみなす語。hookの名前（フォルダー名）は別にリポジトリから集める。
# `webhook`・`useFooHook` など英字の途中の `hook` と、`カスタムフック`・`ウェブフック`・`Reactフック` など
# 直前がカタカナ・英字の `フック` は別のものを指すため除く。`\u30a0-\u30ff` はカタカナ（長音符 `ー` を含む）。
_HOOK_REQUEST_PATTERN = re.compile(
    r'(?<![A-Za-z])hooks?(?![A-Za-z])|(?<![A-Za-z\u30a0-\u30ff])フック|\.github[/\\]hooks'
    r'|pre-?tool-?use|post-?tool-?use|agent-?stop|session-?start'
    r'|user-?prompt-?(?:submitted|transformed)',
    re.IGNORECASE,
)
_EVENTS_DIRECTORY_NAMES = ('events', '-repo-events')


def _find_hooks_directory(cwd):
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


def _get_hook_names(cwd):
    # `helpers/events/<event>/<hook>/`・`helpers/-repo-events/<event>/<hook>/` のhook名を集める。
    hooks_directory = _find_hooks_directory(cwd)
    if hooks_directory is None:
        return set()
    names = set()
    for events_directory_name in _EVENTS_DIRECTORY_NAMES:
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


def _mentions_hooks(prompt, hook_names):
    if _HOOK_REQUEST_PATTERN.search(prompt):
        return True
    lowered = prompt.lower()
    return any(name.lower() in lowered for name in hook_names)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        # 拒否側のpreToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            _utilities_directory, 'hook-change-request-state', 'hook-change-request-state.py',
        ))

        hook_input = _hook_input['read_hook_input']()
        prompt = hook_input.get('prompt')
        if not isinstance(prompt, str):
            raise ValueError('prompt must be a string.')
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)

        # サブエージェントへの依頼は親のエージェントが書いたもので、ユーザーの依頼ではないため記録しない。
        # 記録すると、エージェントがサブエージェントへの依頼でhookの変更を自ら許可できてしまう。
        if hook_input.get('agentId'):
            return 0

        # 一度触れたら、このセッションの間は変更を認めたままにする。
        # 「続けて」「1の順に対応して」など、hookに触れない後続の依頼で作業を止めないため。
        if _mentions_hooks(prompt, _get_hook_names(cwd)):
            state['save_permitted'](state_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to record hook change request: {ex}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
