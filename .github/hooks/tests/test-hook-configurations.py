import json
import os

hooks_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

expected_configurations = []
for filename in sorted(os.listdir(hooks_directory)):
    if not filename.endswith('.json'):
        continue
    path = os.path.join(hooks_directory, filename)
    with open(path, encoding='utf-8') as configuration_file:
        expected_configurations.append((filename, json.load(configuration_file)))

if not expected_configurations:
    raise AssertionError(f'No hook configurations found in {hooks_directory}')

for filename, configuration in expected_configurations:
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

print(f'Validated {len(expected_configurations)} hook configurations.')
