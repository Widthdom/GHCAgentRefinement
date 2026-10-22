import json
import os

hooks_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

expected_configurations = {
    'agentStop.json': {
        'version': 1,
        'hooks': {
            'agentStop': [
                {
                    'type': 'command',
                    'bash': 'python3 .github/hooks/helpers/events/agentStop/debug-add-response-footer/debug-add-response-footer.py',
                    'powershell': 'python .github/hooks/helpers/events/agentStop/debug-add-response-footer/debug-add-response-footer.py',
                },
            ],
        },
    },
    'preToolUse.json': {
        'version': 1,
        'hooks': {
            'preToolUse': [
                {
                    'type': 'command',
                    'matcher': '(?:functions\\.)?(?:bash|powershell)',
                    'bash': 'bash .github/hooks/helpers/events/preToolUse/block-commands/block-commands.sh',
                    'powershell': '& ./.github/hooks/helpers/events/preToolUse/block-commands/block-commands.cmd',
                    'timeoutSec': 10,
                },
            ],
        },
    },
    'sessionStart.json': {
        'version': 1,
        'hooks': {
            'sessionStart': [
                {
                    'type': 'command',
                    'bash': 'python3 .github/hooks/helpers/events/sessionStart/check-git/check-git.py',
                    'powershell': 'python .github/hooks/helpers/events/sessionStart/check-git/check-git.py',
                    'timeoutSec': 10,
                },
                {
                    'type': 'command',
                    'bash': 'bash .github/hooks/helpers/events/sessionStart/check-python/check-python.sh',
                    'powershell': '& ./.github/hooks/helpers/events/sessionStart/check-python/check-python.cmd',
                    'timeoutSec': 10,
                },
                {
                    'type': 'command',
                    'bash': 'python3 .github/hooks/helpers/events/sessionStart/check-ripgrep/check-ripgrep.py',
                    'powershell': 'python .github/hooks/helpers/events/sessionStart/check-ripgrep/check-ripgrep.py',
                    'timeoutSec': 10,
                },
                {
                    'type': 'command',
                    'bash': 'python3 .github/hooks/helpers/events/sessionStart/check-target-session/check-target-session.py',
                    'powershell': 'python .github/hooks/helpers/events/sessionStart/check-target-session/check-target-session.py',
                    'timeoutSec': 10,
                },
                {
                    'type': 'command',
                    'bash': 'python3 .github/hooks/helpers/events/sessionStart/load-copilot-instructions/load-copilot-instructions.py',
                    'powershell': 'python .github/hooks/helpers/events/sessionStart/load-copilot-instructions/load-copilot-instructions.py',
                },
            ],
        },
    },
    'userPromptSubmitted.json': {
        'version': 1,
        'hooks': {
            'userPromptSubmitted': [
                {
                    'type': 'command',
                    'bash': 'python3 .github/hooks/helpers/events/userPromptSubmitted/set-debug-mode/set-debug-mode.py',
                    'powershell': 'python .github/hooks/helpers/events/userPromptSubmitted/set-debug-mode/set-debug-mode.py',
                },
            ],
        },
    },
}

for filename, expected in expected_configurations.items():
    path = os.path.join(hooks_directory, filename)
    with open(path, encoding='utf-8') as configuration_file:
        actual = json.load(configuration_file)
    if actual != expected:
        raise AssertionError(f'Unexpected hook configuration in {filename}: {actual!r}')

print(f'Passed {len(expected_configurations)} hook configuration tests.')
