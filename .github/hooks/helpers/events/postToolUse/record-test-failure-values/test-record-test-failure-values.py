import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'record-test-failure-values.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


def pair_key(expected, actual):
    return hashlib.sha256(json.dumps([expected, actual], ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


class RecordTestFailureValuesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-record-test-failure-values-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.new_session()

    def new_session(self):
        self.session_id = str(uuid.uuid4())
        self.state_path = os.path.join(
            self.root, 'ghc-agent-refinement', 'hook-states', 'test-failure-values', f'{self.session_id}.sqlite3',
        )

    def invoke(self, output, command='python3 -m pytest', tool_name='bash', payload=None, raw=None):
        if raw is None:
            if payload is None:
                payload = {
                    'sessionId': self.session_id,
                    'timestamp': 0,
                    'cwd': self.root,
                    'toolName': tool_name,
                    'toolArgs': {'command': command},
                    'toolResult': {'resultType': 'success', 'textResultForLlm': output},
                }
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=10,
        )

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def read_pairs(self):
        if not os.path.exists(self.state_path):
            return None
        with closing(sqlite3.connect(self.state_path)) as connection:
            return {row[0] for row in connection.execute('SELECT pair FROM pairs')}

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'postToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/postToolUse/record-test-failure-values/record-test-failure-values.py'
        entries = [
            entry for entry in configuration['hooks']['postToolUse']
            if entry.get('bash') == f'python3 {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python {relative_path}')
        for tool_name in ('bash', 'powershell', 'functions.bash'):
            self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        for tool_name in ('edit', 'view'):
            self.assertNotRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')

    def test_directional_formats_are_recorded(self):
        cases = [
            ('jest', '    Expected: "abd"\n    Received: "abc"\n', 'abd', 'abc'),
            ('xunit', 'Assert.Equal() Failure\nExpected: 4\nActual:   3\n', '4', '3'),
            ('nunit', '  Expected: 4\n  But was:  3\n', '4', '3'),
            ('testify', '\tError:  Not equal:\n\t        expected: 4\n\t        actual  : 3\n', '4', '3'),
            ('junit', 'org.opentest4j.AssertionFailedError: expected: <4> but was: <3>\n', '4', '3'),
            ('go got want', '    math_test.go:9: Add(1, 2) = got 3, want 4\n', '4', '3'),
            ('go want got', '    math_test.go:9: want 4, got 3\n', '4', '3'),
        ]
        for name, output, expected, actual in cases:
            with self.subTest(name=name):
                self.new_session()
                self.assert_silent(self.invoke(output))
                pairs = self.read_pairs()
                self.assertIn(pair_key(expected, actual), pairs, name)
                self.assertNotIn(pair_key(actual, expected), pairs, name)

    def test_symmetric_formats_are_recorded_in_both_directions(self):
        cases = [
            ('unittest', "AssertionError: 'abc' != 'abd'\n", 'abc', 'abd'),
            ('pytest', 'E       assert 3 == 4\nE        +  where 3 = add(1, 2)\n', '3', '4'),
            ('rust', "assertion `left == right` failed\n  left: 3\n right: 4\n", '3', '4'),
            ('rust old', 'assertion failed: `(left == right)`\n  left: `3`,\n right: `4`\n', '3', '4'),
        ]
        for name, output, first, second in cases:
            with self.subTest(name=name):
                self.new_session()
                self.assert_silent(self.invoke(output))
                self.assertEqual(self.read_pairs(), {pair_key(first, second), pair_key(second, first)}, name)

    def test_latest_run_replaces_previous_failures(self):
        self.assert_silent(self.invoke('Expected: 4\nReceived: 3\n'))
        self.assert_silent(self.invoke('Expected: 5\nReceived: 6\n', command='npm test'))
        self.assertEqual(self.read_pairs(), {pair_key('5', '6')})
        self.assert_silent(self.invoke('1 passed in 0.01s\n', command='pytest tests'))
        self.assertEqual(self.read_pairs(), set(), 'A passing run must clear the previous failures.')

    def test_skips_without_state_changes(self):
        cases = [
            ('passing run', '3 passed\n', 'pytest', 'bash'),
            ('not a test command', 'Expected: 4\nReceived: 3\n', 'cat log.txt', 'bash'),
            ('word inside another word', 'Expected: 4\nReceived: 3\n', 'echo latest', 'bash'),
            ('not a shell tool', 'Expected: 4\nReceived: 3\n', 'pytest', 'view'),
            ('same values', 'Expected: 4\nReceived: 4\n', 'pytest', 'bash'),
            ('expected without actual', 'Expected: 4\n\n\n\n\nReceived: 3\n', 'pytest', 'bash'),
        ]
        for name, output, command, tool_name in cases:
            with self.subTest(name=name):
                self.new_session()
                self.assert_silent(self.invoke(output, command=command, tool_name=tool_name))
                self.assertIsNone(self.read_pairs(), name)

    def test_other_tool_arguments(self):
        for name, payload in [
            ('json string args', {'toolArgs': json.dumps({'command': 'pytest'})}),
            ('powershell', {'toolName': 'functions.powershell'}),
        ]:
            with self.subTest(name=name):
                self.new_session()
                base = {
                    'sessionId': self.session_id, 'cwd': self.root, 'toolName': 'bash',
                    'toolArgs': {'command': 'pytest'},
                    'toolResult': {'resultType': 'success', 'textResultForLlm': 'Expected: 4\nReceived: 3\n'},
                }
                self.assert_silent(self.invoke('', payload={**base, **payload}))
                self.assertEqual(self.read_pairs(), {pair_key('4', '3')}, name)
        self.new_session()
        self.assert_silent(self.invoke('', payload={
            'sessionId': self.session_id, 'cwd': self.root, 'toolName': 'bash', 'toolArgs': {'command': 'pytest'},
        }))
        self.assertIsNone(self.read_pairs(), 'A missing tool result must be treated as an empty output.')

    def test_values_are_not_stored(self):
        self.assert_silent(self.invoke('Expected: "secret-expected"\nReceived: "secret-actual"\n'))
        with open(self.state_path, 'rb') as file:
            content = file.read()
        self.assertNotIn(b'secret-expected', content)
        self.assertNotIn(b'secret-actual', content)
        self.assertTrue(all(re.fullmatch('[0-9a-f]{64}', pair) for pair in self.read_pairs()))

    def test_invalid_input(self):
        for name, raw in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('invalid utf-8', b'\xff'),
            ('no session', json.dumps({'toolName': 'bash', 'toolArgs': {}}).encode('utf-8')),
            ('invalid session', json.dumps({'sessionId': 'x', 'toolName': 'bash', 'toolArgs': {}}).encode('utf-8')),
            ('no tool name', json.dumps({'sessionId': self.session_id, 'toolArgs': {}}).encode('utf-8')),
            ('no tool args', json.dumps({'sessionId': self.session_id, 'toolName': 'bash'}).encode('utf-8')),
        ]:
            with self.subTest(name=name):
                result = self.invoke('', raw=raw)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to record test failure values:', result.stderr)


if __name__ == '__main__':
    run_tests()
