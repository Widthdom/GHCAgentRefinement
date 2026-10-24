import json
import os
import unittest

from hook_test_output import run_tests


hooks_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class HookConfigurationTests(unittest.TestCase):
    def test_configurations(self):
        filenames = sorted(
            filename for filename in os.listdir(hooks_directory) if filename.endswith('.json')
        )
        self.assertTrue(filenames, f'No hook configurations found in {hooks_directory}')
        for filename in filenames:
            with self.subTest(filename=filename):
                path = os.path.join(hooks_directory, filename)
                with open(path, encoding='utf-8') as configuration_file:
                    configuration = json.load(configuration_file)
                if (
                    not isinstance(configuration, dict)
                    or type(configuration.get('version')) is not int
                    or configuration['version'] != 1
                ):
                    raise AssertionError(f'Invalid hook configuration in {filename}: expected version 1')
                hooks = configuration.get('hooks')
                if not isinstance(hooks, dict) or not hooks:
                    raise AssertionError(f'Invalid hook configuration in {filename}: expected a non-empty hooks object')
                for event_name, event_hooks in hooks.items():
                    if (
                        not isinstance(event_name, str)
                        or not isinstance(event_hooks, list)
                        or any(not isinstance(hook, dict) or hook.get('type') != 'command' for hook in event_hooks)
                    ):
                        raise AssertionError(f'Invalid hook configuration in {filename}: expected command hook arrays by event')


if __name__ == '__main__':
    run_tests()
