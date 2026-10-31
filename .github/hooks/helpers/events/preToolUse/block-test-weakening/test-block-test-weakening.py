import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'block-test-weakening.py')
state_directory = os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-test-weakening')

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

ORIGINAL = (
    'def test_add():\n'
    '    assert add(1, 2) == 3\n'
    '    assert add(0, 0) == 0\n'
    '\n'
    'def test_sub():\n'
    '    assert sub(3, 1) == 2\n'
)


def run_hook(input_bytes, *arguments):
    return subprocess.run([sys.executable, hook_path, *arguments], input=input_bytes, capture_output=True)


class BlockTestWeakeningTests(unittest.TestCase):
    def setUp(self):
        self.directory = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.new_session()
        self.write_file('test_math.py', ORIGINAL)
        self.write_file('math.py', 'def add(a, b):\n    assert a is not None\n    return a + b\n')

    def write_file(self, path, content):
        full_path = os.path.join(self.directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def new_session(self):
        # テストごとに別セッションとし、拒否の記録を持ち越さない。
        self.session_id = str(uuid.uuid4())
        state_path = os.path.join(state_directory, f'{self.session_id}.sqlite3')
        self.addCleanup(lambda: os.path.exists(state_path) and os.remove(state_path))

    def run_calls(self, *tool_calls):
        hook_input = {'sessionId': self.session_id, 'cwd': self.directory, 'toolCalls': list(tool_calls)}
        return run_hook(json.dumps(hook_input).encode('utf-8'))

    def assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)

    def test_weakening_lines_are_denied(self):
        cases = [
            ('FooTests.cs', '[Fact(Skip = "flaky")]'),
            ('FooTests.cs', '[Theory(DisplayName = "x", Skip = "later")]'),
            ('FooTests.cs', '[Ignore]'),
            ('FooTests.cs', '[Ignore("broken")]'),
            ('FooTests.cs', 'Assert.Inconclusive("todo");'),
            ('FooTests.cs', 'Assert.True(true);'),
            ('FooTests.cs', 'Assert.Pass();'),
            ('FooTest.java', '@Disabled'),
            ('test_math.py', '@pytest.mark.skip(reason="flaky")'),
            ('test_math.py', '@pytest.mark.xfail'),
            ('test_math.py', '@unittest.skipIf(True, "x")'),
            ('test_math.py', 'pytest.skip("later")'),
            ('test_math.py', 'self.skipTest("later")'),
            ('test_math.py', 'assert True'),
            ('test_math.py', 'self.assertTrue(True)'),
            ('src/app.test.ts', 'it.skip("adds", () => {'),
            ('src/app.spec.js', 'describe.only("math", () => {'),
            ('src/app.test.tsx', 'xit("adds", () => {'),
            ('src/app.test.ts', 'fdescribe("math", () => {'),
            ('src/app.test.ts', 'expect(true).toBe(true);'),
            ('math_test.go', 't.Skip("later")'),
            ('tests/math.rs', '#[ignore]'),
        ]
        for path, line in cases:
            with self.subTest(path=path, line=line):
                self.new_session()
                self.assert_denied(
                    self.run_calls({'name': 'create', 'args': {'path': path, 'file_text': f'{line}\n'}}),
                    [path, f'"{line}"', 'skip, focus, or trivially pass'], line,
                )

    def test_normal_lines_are_allowed(self):
        lines = [
            '[Fact]',
            '[Theory]',
            'Assert.Equal(3, Add(1, 2));',
            'Assert.True(result);',
            '@pytest.mark.parametrize("a", [1, 2])',
            'it("adds", () => {',
            'describe("math", () => {',
            'expect(add(1, 2)).toBe(3);',
            'assert result',
            'skip = items[1:]',
            'only_once = True',
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assert_allowed(
                    self.run_calls({'name': 'insert', 'args': {'path': 'test_math.py', 'insert_line': 1, 'new_str': f'{line}\n'}}),
                    line,
                )

    def test_test_file_paths(self):
        test_paths = [
            'test_math.py',
            'math_test.py',
            'tests/helpers.py',
            'src/__tests__/app.js',
            'spec/app_helper.rb',
            'Foo.Tests/Helpers.cs',
            'Foo.UnitTests/Helpers.cs',
            'FooTests.cs',
            'FooTest.java',
            'app.test.ts',
            'app.spec.mjs',
            'math_test.go',
            '.github/hooks/helpers/events/preToolUse/x/test-x.py',
        ]
        for path in test_paths:
            with self.subTest(path=path):
                self.new_session()
                self.assert_denied(
                    self.run_calls({'name': 'create', 'args': {'path': path, 'file_text': 'pytest.skip("x")\n'}}),
                    [path], path,
                )
        other_paths = ['math.py', 'src/app.ts', 'Foo/Contest.cs', 'latest.py', 'docs/testing.md', 'Attestation.cs']
        for path in other_paths:
            with self.subTest(path=path):
                self.assert_allowed(
                    self.run_calls({'name': 'create', 'args': {'path': path, 'file_text': 'pytest.skip("x")\n'}}),
                    path,
                )

    def test_existing_weakening_line_is_allowed(self):
        self.write_file('FooTests.cs', '[Fact(Skip = "flaky")]\npublic void A() { }\n')
        self.assert_allowed(
            self.run_calls({'name': 'edit', 'args': {
                'path': 'FooTests.cs', 'old_str': 'public void A() { }', 'new_str': 'public void B() { }',
            }}),
            'A weakening line that the file already contains must be allowed.',
        )
        self.assert_allowed(
            self.run_calls({'name': 'edit', 'args': {
                'path': 'test_math.py', 'old_str': '    pytest.skip("x")\n    a = 1\n', 'new_str': '    pytest.skip("x")\n    a = 2\n',
            }}),
            'A weakening line that the replaced text already contains must be allowed.',
        )

    def test_assertion_reduction_is_denied(self):
        cases = [
            ('edit', {'path': 'test_math.py', 'old_str': '    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n',
                      'new_str': '    assert add(1, 2) == 3\n'}, '2 assertion lines would become 1'),
            ('functions.edit', {'path': 'test_math.py', 'old_str': '    assert sub(3, 1) == 2\n',
                                'new_str': '    sub(3, 1)\n'}, '1 assertion lines would become 0'),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'test_math.py',
                                    'old_str': 'assert sub(3, 1) == 2', 'new_str': 'pass'}, 'would become 0'),
            ('edit', json.dumps({'path': 'test_math.py', 'old_str': 'assert sub(3, 1) == 2', 'new_str': 'pass'}),
             'would become 0'),
            ('create', {'path': 'test_math.py', 'file_text': 'def test_add():\n    assert add(1, 2) == 3\n'},
             '3 assertion lines would become 1'),
            ('apply_patch', '*** Begin Patch\n*** Update File: test_math.py\n@@\n-    assert sub(3, 1) == 2\n+    pass\n'
                            '*** End Patch\n', '1 assertion lines would become 0'),
            ('apply_patch', '*** Begin Patch\n*** Delete File: test_math.py\n*** End Patch\n',
             '3 assertion lines would become 0'),
        ]
        for tool_name, args, expected in cases:
            with self.subTest(tool_name=tool_name, expected=expected):
                self.new_session()
                self.assert_denied(
                    self.run_calls({'name': tool_name, 'args': args}),
                    ['test_math.py would lose assertions', expected], tool_name,
                )

    def test_non_reducing_edits_are_allowed(self):
        cases = [
            ('same count', {'path': 'test_math.py', 'old_str': 'assert add(0, 0) == 0', 'new_str': 'assert add(0, 1) == 1'}),
            ('more', {'path': 'test_math.py', 'old_str': '    assert sub(3, 1) == 2\n',
                      'new_str': '    assert sub(3, 1) == 2\n    assert sub(1, 1) == 0\n'}),
            ('non-test file', {'path': 'math.py', 'old_str': '    assert a is not None\n', 'new_str': ''}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                self.assert_allowed(self.run_calls({'name': 'edit', 'args': args}), name)
        self.assert_allowed(
            self.run_calls({'name': 'create', 'args': {'path': 'test_new.py', 'file_text': 'def test_a():\n    assert a()\n'}}),
            'A new test file must be allowed.',
        )

    def test_denies_only_once(self):
        call = {'name': 'edit', 'args': {'path': 'test_math.py', 'old_str': 'assert sub(3, 1) == 2', 'new_str': 'pass'}}
        self.assert_denied(self.run_calls(call), ['retry the same edit'], 'first')
        self.assert_allowed(self.run_calls(call), 'The same kind of denial must not repeat for the same file.')
        self.assert_denied(
            self.run_calls({'name': 'insert', 'args': {'path': 'test_math.py', 'insert_line': 1, 'new_str': '@pytest.mark.skip\n'}}),
            ['@pytest.mark.skip'], 'A different kind must still be denied once.',
        )

    def test_parallel_calls_are_reported_together(self):
        result = self.run_calls(
            {'name': 'insert', 'args': {'path': 'test_math.py', 'insert_line': 1, 'new_str': '@pytest.mark.skip\n'}},
            {'name': 'create', 'args': {'path': 'src/app.test.ts', 'file_text': 'it.only("a", () => {});\n'}},
        )
        self.assert_denied(result, ['test_math.py', 'src/app.test.ts'], 'parallel')

    def test_view_and_other_tools_are_ignored(self):
        self.assert_allowed(
            self.run_calls(
                {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'test_math.py'}},
                {'name': 'bash', 'args': {'command': 'echo "@pytest.mark.skip" >> test_math.py'}},
            ),
            'Non-writing tool calls must be ignored.',
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
                result = run_hook(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check test weakening:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = json.dumps({
            'sessionId': self.session_id, 'cwd': self.directory, 'toolCalls': [{'name': 'edit', 'args': {}}],
        }).encode('utf-8')
        valid_weakening = [{'name': 'x', 'pattern': 'x'}]
        for name, content in [
            ('not json', '{'),
            ('not object', '[]'),
            ('missing test file patterns', {'assertionPatterns': ['x'], 'weakeningPatterns': valid_weakening}),
            ('empty assertion patterns', {'testFilePatterns': ['x'], 'assertionPatterns': [], 'weakeningPatterns': valid_weakening}),
            ('missing weakening pattern', {'testFilePatterns': ['x'], 'assertionPatterns': ['x'], 'weakeningPatterns': [{'name': 'x'}]}),
            ('invalid test file regex', {'testFilePatterns': ['('], 'assertionPatterns': ['x'], 'weakeningPatterns': valid_weakening}),
            ('invalid weakening regex', {'testFilePatterns': ['x'], 'assertionPatterns': ['x'],
                                         'weakeningPatterns': [{'name': 'x', 'pattern': '('}]}),
        ]:
            with self.subTest(name=name):
                path = os.path.join(self.directory, f'definitions-{name.replace(" ", "-")}.json')
                with open(path, 'w', encoding='utf-8') as file:
                    file.write(content if isinstance(content, str) else json.dumps(content))
                result = run_hook(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check test weakening:', result.stderr)


if __name__ == '__main__':
    run_tests()
