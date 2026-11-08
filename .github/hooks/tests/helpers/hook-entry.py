import json
import os


def get_hook_entry(hook_path):
    # hookの実行スクリプトのパスから、hook定義（.github/hooks/<event-name>.json）の要素を返す。
    # 起動コマンドと定義の配置はtest-hook-configurations.pyで一括して検証するため、個別のテストではhook固有の設定だけを確かめる。
    hook_directory = os.path.dirname(os.path.abspath(hook_path))
    event_directory = os.path.dirname(hook_directory)
    events_directory = os.path.dirname(event_directory)
    hooks_directory = os.path.dirname(os.path.dirname(events_directory))
    event_name = os.path.basename(event_directory)
    prefix = '-repo-' if os.path.basename(events_directory) == '-repo-events' else ''
    with open(os.path.join(hooks_directory, f'{prefix}{event_name}.json'), encoding='utf-8') as file:
        entries = json.load(file)['hooks'][event_name]
    relative_directory = os.path.relpath(hook_directory, os.path.dirname(os.path.dirname(hooks_directory))).replace(os.sep, '/')
    matching = [entry for entry in entries if entry['bash'].split()[-1].rsplit('/', 1)[0] == relative_directory]
    if len(matching) != 1:
        raise AssertionError(f'Expected one hook definition for {relative_directory}, found {len(matching)}.')
    return matching[0]
