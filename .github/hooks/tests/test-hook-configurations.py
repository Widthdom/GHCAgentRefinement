import json
import os
import re
import runpy
import unittest

_run_tests = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'helpers', 'hook-test-output.py'))['run_tests']


_hooks_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_repository_root = os.path.dirname(os.path.dirname(_hooks_directory))
_RUNNER = '.github/hooks/helpers/runner/run-hook.py'
# Pythonが無い環境でも動く必要があるため、ランナーを通さず .sh・.cmd から起動するhook。
_DIRECT_HOOKS = ('block-commands', 'check-python')


def _load_configurations():
    configurations = {}
    for filename in sorted(name for name in os.listdir(_hooks_directory) if name.endswith('.json')):
        with open(os.path.join(_hooks_directory, filename), encoding='utf-8') as file:
            configurations[filename] = json.load(file)
    return configurations


class HookConfigurationTests(unittest.TestCase):
    def test_configurations(self):
        configurations = _load_configurations()
        self.assertTrue(configurations, f'No hook configurations found in {_hooks_directory}')
        for filename, configuration in configurations.items():
            with self.subTest(filename=filename):
                if (
                    not isinstance(configuration, dict)
                    or type(configuration.get('version')) is not int
                    or configuration['version'] != 1
                ):
                    raise AssertionError(f'Invalid hook configuration in {filename}: expected version 1')
                hooks = configuration.get('hooks')
                # 定義ファイルは、名前のイベント（-repo-<event-name>.json は <event-name>）だけを持つ。
                event_name = filename.removeprefix('-repo-').removesuffix('.json')
                if not isinstance(hooks, dict) or list(hooks) != [event_name]:
                    raise AssertionError(f'Invalid hook configuration in {filename}: expected only the {event_name} event')
                event_hooks = hooks[event_name]
                if (
                    not isinstance(event_hooks, list)
                    or not event_hooks
                    or any(not isinstance(hook, dict) or hook.get('type') != 'command' for hook in event_hooks)
                ):
                    raise AssertionError(f'Invalid hook configuration in {filename}: expected a non-empty command hook array')

    def test_commands(self):
        for filename, configuration in _load_configurations().items():
            # このリポジトリ専用のhookの定義（-repo-<event-name>.json）は helpers/-repo-events 配下のhookを起動する。
            events_name = '-repo-events' if filename.startswith('-repo-') else 'events'
            for event_name, entries in configuration['hooks'].items():
                script_paths = []
                for entry in entries:
                    script_path = entry['bash'].split()[-1]
                    script_paths.append(script_path)
                    hook_name = script_path.split('/')[-2]
                    hook_path = f'.github/hooks/helpers/{events_name}/{event_name}/{hook_name}/{hook_name}'
                    with self.subTest(filename=filename, hook_name=hook_name):
                        if hook_name in _DIRECT_HOOKS:
                            expected = (f'bash {hook_path}.sh', f'& ./{hook_path}.cmd')
                        else:
                            expected = (f'python3 {_RUNNER} {hook_path}.py', f'python {_RUNNER} {hook_path}.py')
                        self.assertEqual((entry['bash'], entry['powershell']), expected)
                        if 'matcher' in entry:
                            re.compile(entry['matcher'])
                        if 'timeoutSec' in entry:
                            self.assertIs(type(entry['timeoutSec']), int)
                            self.assertGreater(entry['timeoutSec'], 0)
                        self.assertTrue(os.path.isfile(os.path.join(_repository_root, *script_path.split('/'))))
                # 要素は実行スクリプトのパスの昇順に並べる。
                with self.subTest(filename=filename):
                    self.assertEqual(script_paths, sorted(script_paths))

    def test_every_hook_is_configured_once(self):
        configured = []
        for filename, configuration in _load_configurations().items():
            events_name = '-repo-events' if filename.startswith('-repo-') else 'events'
            for event_name, entries in configuration['hooks'].items():
                configured.extend(f'{events_name}/{event_name}/{entry["bash"].split()[-1].split("/")[-2]}' for entry in entries)
        hook_directories = []
        for events_name in ('events', '-repo-events'):
            events_directory = os.path.join(_hooks_directory, 'helpers', events_name)
            for event_name in sorted(os.listdir(events_directory)):
                hook_directories.extend(
                    f'{events_name}/{event_name}/{hook_name}'
                    for hook_name in sorted(os.listdir(os.path.join(events_directory, event_name)))
                    if os.path.isdir(os.path.join(events_directory, event_name, hook_name))
                )
        # 定義のないhookのフォルダー、フォルダーのない定義、重複した定義を検出する。
        self.assertEqual(sorted(configured), sorted(hook_directories))


if __name__ == '__main__':
    _run_tests()
