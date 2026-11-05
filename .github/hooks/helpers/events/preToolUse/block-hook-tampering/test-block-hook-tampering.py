import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-hook-tampering.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class BlockHookTamperingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-hook-tampering-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__directory = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__directory)
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__root, 'ghc-agent-refinement', 'hook-states', 'hook-change-requests', f'{self.__session_id}.json',
        )

    def __write_state(self, content):
        os.makedirs(os.path.dirname(self.__state_path), exist_ok=True)
        with open(self.__state_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content if isinstance(content, str) else json.dumps(content))

    def __run_process(self, hook_input):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __run_call(self, tool_name, tool_args):
        return self.__run_process({
            'sessionId': self.__session_id, 'timestamp': 0, 'cwd': self.__directory,
            'toolName': tool_name, 'toolArgs': tool_args,
        })

    def __assert_allowed(self, result, message=''):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_denied(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny')
        reason = output['permissionDecisionReason']
        self.assertEqual(result.stderr.decode('utf-8').strip(), reason)
        self.assertTrue(reason.startswith('Hook change denied: '))
        self.assertIn('ask them to request the hook change explicitly', reason)
        for fragment in fragments:
            self.assertIn(fragment, reason)
        return reason

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'preToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/preToolUse/block-hook-tampering/block-hook-tampering.py'
        entries = configuration['hooks']['preToolUse']
        matching = [
            entry for entry in entries if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['timeoutSec'], 10)
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch', 'bash', 'functions.powershell'):
            self.assertRegex(tool_name, f'^(?:{matching[0]["matcher"]})$')
        self.assertNotRegex('view', f'^(?:{matching[0]["matcher"]})$')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_edit_tools_on_hook_files_are_denied(self):
        cases = (
            ('edit', {'path': '.github/hooks/preToolUse.json', 'old_str': 'a', 'new_str': 'b'}, '.github/hooks/preToolUse.json'),
            ('create', {'path': os.path.join(self.__directory, '.github', 'hooks', 'helpers', 'events', 'x', 'y', 'y.py'), 'file_text': ''},
             '.github/hooks/helpers/events/x/y/y.py'),
            ('functions.edit', json.dumps({'path': '.github/Hooks/agentStop.json'}), '.github/Hooks/agentStop.json'),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'sub/../.github/hooks/a.json'}, '.github/hooks/a.json'),
            ('insert', {'path': '.github/hooks/helpers/-repo-events/p/q/q.py', 'insert_line': 1, 'new_str': 'x'},
             '.github/hooks/helpers/-repo-events/p/q/q.py'),
            ('apply_patch', '*** Begin Patch\n*** Delete File: .github/hooks/preToolUse.json\n*** End Patch\n', '.github/hooks/preToolUse.json'),
            ('apply_patch', '*** Begin Patch\n*** Update File: src/a.py\n*** Move to: .github/hooks/b.py\n*** End Patch\n', '.github/hooks/b.py'),
            ('create', {'path': os.path.join(self.__root, 'ghc-agent-refinement', 'hook-states', 'hook-change-requests', 'x.json'), 'file_text': '{}'},
             'ghc-agent-refinement/hook-states/hook-change-requests/x.json'),
        )
        for tool_name, tool_args, target in cases:
            with self.subTest(tool_name=tool_name, target=target):
                self.__assert_denied(self.__run_call(tool_name, tool_args), target)

    def test_shell_modifications_of_hook_files_are_denied(self):
        commands = (
            'rm -rf .github/hooks/helpers/events/preToolUse/block-commands',
            'git checkout -- .github/hooks/preToolUse.json',
            'git -C . restore .github/hooks',
            'mv .github/hooks/agentStop.json /tmp/x',
            "sed -i 's/deny/allow/' .github/hooks/helpers/events/preToolUse/x/x.py",
            'echo {} > .github/hooks/preToolUse.json',
            'echo "{\\"permitted\\":true}" >> "$TMPDIR/ghc-agent-refinement/hook-states/hook-change-requests/a.json"',
            'Remove-Item -Recurse .github\\hooks\\helpers',
            'Set-Content -Path .github/hooks/x.json -Value "{}"',
            "python -c \"open('.github/hooks/x.json', 'w').write('{}')\"",
            "python3 -c \"import pathlib; pathlib.Path('.github/hooks/x.json').write_text('')\"",
            "[IO.File]::WriteAllText('.github/hooks/x.json', '')",
            'cd .github/hooks && rm preToolUse.json',
        )
        for command in commands:
            for tool_name in ('bash', 'powershell'):
                with self.subTest(command=command, tool_name=tool_name):
                    self.__assert_denied(self.__run_call(tool_name, {'command': command}), f'command `{command}`')

    def test_reading_and_running_hooks_are_allowed(self):
        commands = (
            'cat .github/hooks/preToolUse.json',
            'python .github/hooks/tests/test-all-hooks.py',
            'python .github/hooks/tests/test-all-hooks.py > /tmp/all.log 2>&1',
            'python .github/hooks/tests/test-all-hooks.py 2>&1 | tail -5',
            'git diff -- .github/hooks',
            'git log --oneline -- .github/hooks',
            'grep -rn deny .github/hooks',
            'Get-Content .github\\hooks\\agentStop.json',
            'rm -rf build/',
            'echo x > notes.txt',
        )
        for command in commands:
            with self.subTest(command=command):
                self.__assert_allowed(self.__run_call('bash', {'command': command}), command)

    def test_other_files_and_tools_are_allowed(self):
        cases = (
            ('edit', {'path': 'src/hooks/use-state.ts'}),
            ('edit', {'path': '.github/skills/create-hook/SKILL.md'}),
            ('create', {'path': 'docs/.github-hooks.md'}),
            ('str_replace_editor', {'command': 'view', 'path': '.github/hooks/preToolUse.json'}),
            ('view', {'path': '.github/hooks/preToolUse.json'}),
            ('apply_patch', '*** Begin Patch\n*** Update File: src/a.py\n@@\n-a\n+.github/hooks\n*** End Patch\n'),
            ('bash', 'not json'),
        )
        for tool_name, tool_args in cases:
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                self.__assert_allowed(self.__run_call(tool_name, tool_args))
        self.assertFalse(os.path.exists(self.__state_path))

    def test_permitted_session_is_allowed(self):
        self.__write_state({'updatedAt': '2026-01-01T00:00:00+00:00', 'permitted': True})
        self.__assert_allowed(self.__run_call('edit', {'path': '.github/hooks/preToolUse.json'}))
        self.__assert_allowed(self.__run_call('bash', {'command': 'rm .github/hooks/x.json'}))

    def test_state_without_true_permission_is_denied(self):
        for state in ({'permitted': False}, {'permitted': 'true'}, {}):
            with self.subTest(state=state):
                self.__write_state(state)
                self.__assert_denied(self.__run_call('edit', {'path': '.github/hooks/preToolUse.json'}))

    def test_other_sessions_permission_does_not_apply(self):
        self.__write_state({'permitted': True})
        self.__session_id = str(uuid.uuid4())
        self.__assert_denied(self.__run_call('edit', {'path': '.github/hooks/preToolUse.json'}))

    def test_long_command_is_truncated(self):
        command = 'rm .github/hooks/x.json ' + 'y' * 300
        reason = self.__assert_denied(self.__run_call('bash', {'command': command}))
        self.assertIn(command[:200] + '...', reason)
        self.assertNotIn(command, reason)

    def test_invalid_input(self):
        def encode(value):
            return json.dumps(value).encode('utf-8')

        call = {'toolName': 'edit', 'toolArgs': {}}
        for name, hook_input in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('invalid utf-8', b'\xff'),
            ('no session', encode({'cwd': self.__directory, **call})),
            ('invalid session', encode({'sessionId': 'x', 'cwd': self.__directory, **call})),
            ('no cwd', encode({'sessionId': self.__session_id, **call})),
            ('relative cwd', encode({'sessionId': self.__session_id, 'cwd': 'relative', **call})),
            ('no tool args', encode({'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit'})),
            ('tool name not string', encode({'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 1, 'toolArgs': {}})),
        ]:
            with self.subTest(name=name):
                result = self.__run_process(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check hook tampering:', result.stderr)

    def test_invalid_state_reports_error(self):
        for content in ('{', '[]'):
            with self.subTest(content=content):
                self.__write_state(content)
                result = self.__run_call('edit', {'path': '.github/hooks/preToolUse.json'})
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to check hook tampering:', result.stderr)


if __name__ == '__main__':
    run_tests()
