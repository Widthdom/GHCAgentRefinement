import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'record-viewed-commits.py')

_helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_hooks_directory = os.path.dirname(_helpers_directory)
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_get_hook_entry = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-entry.py'))['get_hook_entry']

_state = runpy.run_path(os.path.join(_helpers_directory, 'utilities', 'viewed-commits-state', 'viewed-commits-state.py'))

_COMMIT_A = '0123456789abcdef0123456789abcdef01234567'
_COMMIT_B = 'fedcba9876543210fedcba9876543210fedcba98'
_SHOW_OUTPUT = (
    f'commit {_COMMIT_A}\nAuthor: Test <test@example.com>\n\n    fix: guard division by zero\n\n'
    'diff --git a/src/calc.py b/src/calc.py\n'
)


class RecordViewedCommitsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-record-viewed-commits-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__root, 'ghc-agent-refinement', 'hook-states', 'viewed-commits', f'{self.__session_id}.sqlite3',
        )

    def __run_hook(self, hook_input):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.__environment, timeout=10,
        )

    def __run_call(self, tool_name, tool_args, output):
        result = self.__run_hook({
            'sessionId': self.__session_id, 'cwd': self.__root, 'toolName': tool_name, 'toolArgs': tool_args,
            'toolResult': {'resultType': 'success', 'textResultForLlm': output},
        })
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def test_configuration(self):
        entry = _get_hook_entry(_hook_path)
        for tool_name in ('bash', 'functions.powershell'):
            self.assertRegex(tool_name, f'^(?:{entry["matcher"]})$')
        self.assertNotRegex('edit', f'^(?:{entry["matcher"]})$')

    def test_viewed_commits_are_recorded(self):
        cases = [
            ('git show', 'bash', {'command': 'git show 0123456'}, _SHOW_OUTPUT),
            ('JSON string arguments', 'functions.bash', json.dumps({'command': 'git show 0123456'}), _SHOW_OUTPUT),
            ('git -C show', 'powershell', {'command': 'git -C src --no-pager show 0123456 | Select-Object -First 50'},
             _SHOW_OUTPUT),
            ('git log with decorations and color', 'bash', {'command': 'git log -p -- src/calc.py'},
             f'\x1b[33mcommit {_COMMIT_A}\x1b[m (HEAD -> main)\n    fix\n\ncommit {_COMMIT_B}\n    Add calculator\n'),
        ]
        for name, tool_name, args, output in cases:
            with self.subTest(name=name):
                if os.path.exists(self.__state_path):
                    os.remove(self.__state_path)
                self.__run_call(tool_name, args, output)
                expected = {_COMMIT_A, _COMMIT_B} if 'log' in name else {_COMMIT_A}
                self.assertEqual(_state['get_viewed'](self.__state_path), expected, name)

    def test_other_calls_are_not_recorded(self):
        cases = [
            ('oneline list', 'bash', {'command': 'git log --oneline'}, '0123456 fix: guard division by zero\n'),
            ('not a view command', 'bash', {'command': 'git blame src/calc.py'}, f'commit {_COMMIT_A}\n'),
            ('commit header in other command output', 'bash', {'command': 'cat notes.txt'}, f'commit {_COMMIT_A}\n'),
            ('git show failed', 'bash', {'command': 'git show 0123456'}, "fatal: bad object 0123456\n"),
            ('edit tool', 'edit', {'path': 'a.txt', 'new_str': f'commit {_COMMIT_A}'}, ''),
            ('non-object arguments', 'bash', 'git show', _SHOW_OUTPUT),
        ]
        for name, tool_name, args, output in cases:
            with self.subTest(name=name):
                self.__run_call(tool_name, args, output)
        self.assertFalse(os.path.exists(self.__state_path), 'Nothing must be recorded.')

    def test_invalid_input_exits_with_error(self):
        cases = [
            ('empty input', b''),
            ('invalid JSON', b'{'),
            ('non-object', b'[]'),
            ('missing sessionId', {'toolName': 'bash', 'toolArgs': {}}),
            ('invalid sessionId', {'sessionId': 'abc', 'toolName': 'bash', 'toolArgs': {}}),
            ('missing toolName', {'sessionId': self.__session_id, 'toolArgs': {}}),
            ('missing toolArgs', {'sessionId': self.__session_id, 'toolName': 'bash'}),
        ]
        for name, hook_input in cases:
            with self.subTest(name=name):
                result = self.__run_hook(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to record viewed commits', result.stderr, name)


if __name__ == '__main__':
    run_tests()
