import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-snapshot-rubber-stamp.py')
_definitions_path = os.path.join(_script_directory, 'snapshot-update-definitions.json')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_get_hook_entry = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-entry.py'))['get_hook_entry']


class BlockSnapshotRubberStampTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-snapshot-rubber-stamp-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__new_session()

    def __new_session(self):
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__root, 'ghc-agent-refinement', 'hook-states', 'block-snapshot-rubber-stamp', f'{self.__session_id}.sqlite3',
        )

    def __run_hook(self, hook_input, *arguments):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __run_command(self, command, tool_name='bash'):
        return self.__run_hook({
            'sessionId': self.__session_id, 'cwd': self.__root, 'toolName': tool_name, 'toolArgs': {'command': command},
        })

    def __assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)

    def test_definitions_are_valid_json(self):
        with open(_definitions_path, encoding='utf-8') as file:
            definitions = json.load(file)
        self.assertTrue(definitions['updateCommands'])

    def test_configuration(self):
        entry = _get_hook_entry(_hook_path)
        for tool_name in ('bash', 'powershell', 'functions.bash'):
            self.assertRegex(tool_name, f'^(?:{entry["matcher"]})$')
        self.assertNotRegex('edit', f'^(?:{entry["matcher"]})$')

    def test_bulk_snapshot_updates_are_denied(self):
        cases = [
            ('npx jest -u', 'Jest/Vitest/Playwright'),
            ('npm test -- -u', 'Jest/Vitest/Playwright'),
            ('pnpm run test --update', 'Jest/Vitest/Playwright'),
            ('npx vitest run -u src', 'Jest/Vitest/Playwright'),
            ('npx playwright test --update-snapshots', '--update-snapshots'),
            ('npx jest --updateSnapshot', '--updateSnapshot'),
            ('python3 -m pytest --snapshot-update tests', '--snapshot-update'),
            ('pytest --force-regen', '--force-regen'),
            ('pytest --inline-snapshot=fix,create', 'inline-snapshot'),
            ('cargo insta accept', 'insta'),
            ('cargo insta test --accept', 'insta'),
            ('INSTA_UPDATE=always cargo test', 'insta'),
            ('dotnet verify accept -y', 'Verify'),
            ('go test ./... -update', 'Go golden'),
            ('go test ./pkg -run TestRender -update-golden=true', 'Go golden'),
            ('UPDATE_SNAPSHOTS=1 python3 -m unittest', 'environment variable'),
            ('cd app && UPDATE_GOLDEN=true make test 2>&1', 'environment variable'),
        ]
        for command, kind in cases:
            with self.subTest(command=command):
                self.__new_session()
                self.__assert_denied(self.__run_command(command), [kind, 'only once per session'], command)
                self.assertTrue(os.path.isfile(self.__state_path), command)

    def test_powershell_environment_variable_is_denied(self):
        self.__assert_denied(
            self.__run_command("$env:UPDATE_SNAPSHOTS = '1'; dotnet test", tool_name='powershell'),
            ['environment variable'], 'PowerShell environment assignment must be detected.',
        )

    def test_ordinary_commands_are_allowed(self):
        cases = [
            'npx jest',
            'npm test',
            'python3 -u -m pytest',
            'git push -u origin main; npm test',
            'sort -u names.txt | npx jest --listTests',
            'npm update',
            'go test ./... -run TestUpdate',
            'pytest -k update_snapshot_name',
            'echo UPDATE_SNAPSHOTS=0',
            'cargo insta test',
        ]
        for command in cases:
            with self.subTest(command=command):
                self.__assert_allowed(self.__run_command(command), command)
        self.assertFalse(os.path.exists(self.__state_path), 'Allowed commands must not create the state file.')

    def test_same_kind_is_denied_only_once_per_session(self):
        self.__assert_denied(self.__run_command('npx jest -u'), ['Jest/Vitest/Playwright'], 'First update must be denied.')
        self.__assert_allowed(self.__run_command('npx jest -u'), 'Retry must be allowed.')
        self.__assert_allowed(self.__run_command('npm test -- -u'), 'Same kind must be allowed after the first denial.')
        self.__assert_denied(self.__run_command('cargo insta accept'), ['insta'], 'Another kind must still be denied.')
        self.__new_session()
        self.__assert_denied(self.__run_command('npx jest -u'), ['Jest/Vitest/Playwright'], 'A new session must deny again.')

    def test_other_tools_are_ignored(self):
        result = self.__run_hook({
            'sessionId': self.__session_id, 'cwd': self.__root, 'toolName': 'edit',
            'toolArgs': {'path': 'a.txt', 'new_str': 'npx jest -u'},
        })
        self.__assert_allowed(result, 'Non-shell tools must be ignored.')

    def test_json_string_tool_args_are_parsed(self):
        result = self.__run_hook({
            'sessionId': self.__session_id, 'cwd': self.__root, 'toolName': 'bash',
            'toolArgs': json.dumps({'command': 'npx jest -u'}),
        })
        self.__assert_denied(result, ['Jest/Vitest/Playwright'], 'JSON string toolArgs must be parsed.')

    def test_invalid_input_exits_with_error(self):
        cases = [
            ('empty input', b''),
            ('invalid JSON', b'{'),
            ('non-object', b'[]'),
            ('missing sessionId', {'toolName': 'bash', 'toolArgs': {'command': 'npx jest -u'}}),
            ('invalid sessionId', {'sessionId': 'abc', 'toolName': 'bash', 'toolArgs': {}}),
            ('missing toolName', {'sessionId': self.__session_id, 'toolArgs': {}}),
            ('missing toolArgs', {'sessionId': self.__session_id, 'toolName': 'bash'}),
        ]
        for name, hook_input in cases:
            with self.subTest(name=name):
                result = self.__run_hook(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check snapshot updates', result.stderr, name)

    def test_invalid_definitions_exit_with_error(self):
        cases = [
            ('not an object', '[]'),
            ('empty array', '{"updateCommands": []}'),
            ('missing pattern', '{"updateCommands": [{"name": "x"}]}'),
            ('invalid regex', '{"updateCommands": [{"name": "x", "pattern": "("}]}'),
        ]
        for name, content in cases:
            with self.subTest(name=name):
                path = os.path.join(self.__root, 'definitions.json')
                with open(path, 'w', encoding='utf-8', newline='\n') as file:
                    file.write(content)
                result = self.__run_hook(
                    {'sessionId': self.__session_id, 'toolName': 'bash', 'toolArgs': {'command': 'npx jest -u'}},
                    '--definitions-path', path,
                )
                self.assertEqual(result.returncode, 2, name)
        missing = self.__run_hook(
            {'sessionId': self.__session_id, 'toolName': 'bash', 'toolArgs': {'command': 'ls'}},
            '--definitions-path', os.path.join(self.__root, 'missing.json'),
        )
        self.assertEqual(missing.returncode, 2, 'A missing definitions file must fail.')


if __name__ == '__main__':
    _run_tests()
