import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'block-goalpost-moving.py')
recorder_path = os.path.join(
    os.path.dirname(os.path.dirname(script_directory)), 'postToolUse', 'record-test-failure-values',
    'record-test-failure-values.py',
)
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

ORIGINAL = (
    'def test_add():\n'
    '    assert add(1, 2) == 4\n'
    '    assert greet("a") == "hello a"\n'
)


class BlockGoalpostMovingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-goalpost-moving-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.directory = os.path.join(self.root, 'workspace')
        os.makedirs(self.directory)
        self.write_file('test_math.py', ORIGINAL)
        self.write_file('math.py', 'def add(a, b):\n    return 3\n')
        self.new_session()

    def write_file(self, path, content):
        full_path = os.path.join(self.directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def new_session(self):
        self.session_id = str(uuid.uuid4())
        self.state_path = os.path.join(
            self.root, 'ghc-agent-refinement', 'hook-states', 'test-failure-values', f'{self.session_id}.sqlite3',
        )

    def run_process(self, path, hook_input, *arguments):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=10,
        )

    def record_failure(self, output, command='python3 -m pytest'):
        # 記録側のhookを実際に通し、状態の形式の食い違いも検出する。
        result = self.run_process(recorder_path, {
            'sessionId': self.session_id, 'cwd': self.directory, 'toolName': 'bash',
            'toolArgs': {'command': command},
            'toolResult': {'resultType': 'success', 'textResultForLlm': output},
        })
        self.assertEqual((result.returncode, result.stderr), (0, b''))

    def run_calls(self, *tool_calls):
        return self.run_process(
            hook_path, {'sessionId': self.session_id, 'cwd': self.directory, 'toolCalls': list(tool_calls)},
        )

    def assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'preToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/preToolUse/block-goalpost-moving/block-goalpost-moving.py'
        entries = [
            entry for entry in configuration['hooks']['preToolUse'] if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        self.assertNotRegex('bash', f'^(?:{entries[0]["matcher"]})$')

    def test_rewriting_expected_to_actual_is_denied(self):
        cases = [
            ('edit', {'path': 'test_math.py', 'old_str': '    assert add(1, 2) == 4\n',
                      'new_str': '    assert add(1, 2) == 3\n'}),
            ('functions.edit', json.dumps({'path': 'test_math.py', 'old_str': 'assert add(1, 2) == 4',
                                           'new_str': 'assert add(1, 2) == 3'})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'test_math.py',
                                    'old_str': 'assert add(1, 2) == 4', 'new_str': 'assert add(1, 2) == 3'}),
            ('create', {'path': 'test_math.py', 'file_text': ORIGINAL.replace('== 4', '== 3')}),
            ('apply_patch', '*** Begin Patch\n*** Update File: test_math.py\n@@\n-    assert add(1, 2) == 4\n'
                            '+    assert add(1, 2) == 3\n*** End Patch\n'),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name):
                self.new_session()
                self.record_failure('E       assert 3 == 4\n')
                self.assert_denied(
                    self.run_calls({'name': tool_name, 'args': args}),
                    ['test_math.py would replace the expected value 4 with 3', 'retry the same edit'], tool_name,
                )

    def test_string_literals_with_other_quotes_are_matched(self):
        self.record_failure('    Expected: "hello a"\n    Received: "hi a"\n', command='npx jest')
        self.assert_denied(
            self.run_calls({'name': 'edit', 'args': {
                'path': 'test_math.py', 'old_str': 'greet("a") == "hello a"', 'new_str': "greet(\"a\") == 'hi a'",
            }}),
            ['"hello a"', "'hi a'"], 'Quotes must not affect the comparison.',
        )

    def test_unrelated_edits_are_allowed(self):
        self.record_failure('Expected: 4\nReceived: 3\n')
        cases = [
            ('fixing the implementation', {'path': 'math.py', 'old_str': 'return 3', 'new_str': 'return 4'}),
            ('implementation gets the actual value', {'path': 'math.py', 'old_str': 'return 4', 'new_str': 'return 3'}),
            ('other value', {'path': 'test_math.py', 'old_str': 'add(1, 2) == 4', 'new_str': 'add(1, 2) == 5'}),
            ('other line shape', {'path': 'test_math.py', 'old_str': 'assert add(1, 2) == 4',
                                  'new_str': 'assert add(1, 3) == 4\n    assert sub(4) == 3'}),
            ('actual back to expected', {'path': 'test_math.py', 'old_str': 'add(1, 2) == 3', 'new_str': 'add(1, 2) == 4'}),
            ('number inside a name', {'path': 'test_math.py', 'old_str': 'value4 = x', 'new_str': 'value3 = x'}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                self.assert_allowed(self.run_calls({'name': 'edit', 'args': args}), name)
        self.assert_allowed(
            self.run_calls({'name': 'insert', 'args': {'path': 'test_math.py', 'insert_line': 1, 'new_str': '    x = 3\n'}}),
            'An insertion has no replaced value.',
        )

    def test_no_recorded_failure_is_allowed(self):
        call = {'name': 'edit', 'args': {'path': 'test_math.py', 'old_str': '== 4', 'new_str': '== 3'}}
        self.assert_allowed(self.run_calls(call), 'No state file.')
        self.assertFalse(os.path.exists(self.state_path), 'The check must not create a state file.')
        self.record_failure('Expected: 4\nReceived: 3\n')
        self.record_failure('1 passed\n')
        self.assert_allowed(self.run_calls(call), 'A passing run clears the recorded failure.')

    def test_denies_only_once_per_file(self):
        self.record_failure('Expected: 4\nReceived: 3\n')
        self.write_file('tests/test_other.py', 'assert other() == 4\n')
        call = {'name': 'edit', 'args': {'path': 'test_math.py', 'old_str': 'add(1, 2) == 4', 'new_str': 'add(1, 2) == 3'}}
        self.assert_denied(self.run_calls(call), ['test_math.py'], 'first')
        self.assert_allowed(self.run_calls(call), 'The same file must not be denied twice.')
        self.assert_denied(
            self.run_calls({'name': 'edit', 'args': {
                'path': 'tests/test_other.py', 'old_str': 'other() == 4', 'new_str': 'other() == 3',
            }}),
            ['test_other.py'], 'Another file must still be denied once.',
        )

    def test_parallel_calls_are_reported_together(self):
        self.record_failure('Expected: 4\nReceived: 3\n')
        self.write_file('src/app.test.ts', 'expect(add(1, 2)).toBe(4);\n')
        result = self.run_calls(
            {'name': 'edit', 'args': {'path': 'test_math.py', 'old_str': 'add(1, 2) == 4', 'new_str': 'add(1, 2) == 3'}},
            {'name': 'edit', 'args': {'path': 'src/app.test.ts', 'old_str': 'toBe(4)', 'new_str': 'toBe(3)'}},
        )
        self.assert_denied(result, ['test_math.py', 'app.test.ts'], 'parallel')

    def test_non_test_files_and_other_tools_are_ignored(self):
        self.record_failure('Expected: 4\nReceived: 3\n')
        self.assert_allowed(
            self.run_calls(
                {'name': 'edit', 'args': {'path': 'docs/latest.md', 'old_str': 'x == 4', 'new_str': 'x == 3'}},
                {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'test_math.py'}},
                {'name': 'bash', 'args': {'command': 'sed -i s/4/3/ test_math.py'}},
                {'name': 'apply_patch', 'args': '*** Begin Patch\n*** Delete File: test_math.py\n*** End Patch\n'},
            ),
            'Non-test files, non-writing tools, and deletions must be ignored.',
        )

    def test_invalid_input(self):
        def encode(value):
            return json.dumps(value).encode('utf-8')

        calls = [{'name': 'edit', 'args': {}}]
        for name, hook_input in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('no session', encode({'cwd': self.directory, 'toolCalls': calls})),
            ('invalid session', encode({'sessionId': 'x', 'cwd': self.directory, 'toolCalls': calls})),
            ('no cwd', encode({'sessionId': self.session_id, 'toolCalls': calls})),
            ('relative cwd', encode({'sessionId': self.session_id, 'cwd': 'relative', 'toolCalls': calls})),
            ('no tool calls', encode({'sessionId': self.session_id, 'cwd': self.directory})),
            ('tool call not object', encode({'sessionId': self.session_id, 'cwd': self.directory, 'toolCalls': ['edit']})),
            ('no tool name', encode({'sessionId': self.session_id, 'cwd': self.directory, 'toolCalls': [{'args': {}}]})),
            ('invalid utf-8', b'\xff'),
        ]:
            with self.subTest(name=name):
                result = self.run_process(hook_path, hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check goalpost moving:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = {'sessionId': self.session_id, 'cwd': self.directory, 'toolCalls': [{'name': 'edit', 'args': {}}]}
        for name, content in [
            ('missing file', None),
            ('not json', '{'),
            ('not object', '[]'),
            ('missing test file patterns', {'assertionPatterns': ['x']}),
            ('empty test file patterns', {'testFilePatterns': []}),
            ('invalid regex', {'testFilePatterns': ['(']}),
        ]:
            with self.subTest(name=name):
                path = os.path.join(self.root, f'definitions-{name.replace(" ", "-")}.json')
                if content is not None:
                    with open(path, 'w', encoding='utf-8') as file:
                        file.write(content if isinstance(content, str) else json.dumps(content))
                result = self.run_process(hook_path, valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check goalpost moving:', result.stderr)


if __name__ == '__main__':
    run_tests()
