import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-test-weakening.py')
_state_directory = os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-test-weakening')

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_ORIGINAL = (
    'def test_add():\n'
    '    assert add(1, 2) == 3\n'
    '    assert add(0, 0) == 0\n'
    '\n'
    'def test_sub():\n'
    '    assert sub(3, 1) == 2\n'
)


def _run_hook(input_bytes, *arguments):
    return subprocess.run([sys.executable, _hook_path, *arguments], input=input_bytes, capture_output=True)


class BlockTestWeakeningTests(unittest.TestCase):
    def setUp(self):
        self.__directory = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.__directory, ignore_errors=True)
        self.__new_session()
        self.__write_file('test_math.py', _ORIGINAL)
        self.__write_file('math.py', 'def add(a, b):\n    assert a is not None\n    return a + b\n')

    def __write_file(self, path, content):
        full_path = os.path.join(self.__directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __new_session(self):
        # テストごとに別セッションとし、拒否の記録を持ち越さない。
        self.__session_id = str(uuid.uuid4())
        state_path = os.path.join(_state_directory, f'{self.__session_id}.sqlite3')
        self.addCleanup(lambda: os.path.exists(state_path) and os.remove(state_path))

    def __run_call(self, tool_call):
        hook_input = {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': tool_call['name'], 'toolArgs': tool_call['args']}
        return _run_hook(json.dumps(hook_input).encode('utf-8'))

    def __assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_denied(self, result, expected_parts, message):
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
                self.__new_session()
                self.__assert_denied(
                    self.__run_call({'name': 'create', 'args': {'path': path, 'file_text': f'{line}\n'}}),
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
                self.__assert_allowed(
                    self.__run_call({'name': 'insert', 'args': {'path': 'test_math.py', 'insert_line': 1, 'new_str': f'{line}\n'}}),
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
                self.__new_session()
                self.__assert_denied(
                    self.__run_call({'name': 'create', 'args': {'path': path, 'file_text': 'pytest.skip("x")\n'}}),
                    # 理由のパスは、OSによらず `/` 区切りで示される。
                    [path], path,
                )
        other_paths = ['math.py', 'src/app.ts', 'Foo/Contest.cs', 'latest.py', 'docs/testing.md', 'Attestation.cs']
        for path in other_paths:
            with self.subTest(path=path):
                self.__assert_allowed(
                    self.__run_call({'name': 'create', 'args': {'path': path, 'file_text': 'pytest.skip("x")\n'}}),
                    path,
                )

    def test_existing_weakening_line_is_allowed(self):
        self.__write_file('FooTests.cs', '[Fact(Skip = "flaky")]\npublic void A() { }\n')
        self.__assert_allowed(
            self.__run_call({'name': 'edit', 'args': {
                'path': 'FooTests.cs', 'old_str': 'public void A() { }', 'new_str': 'public void B() { }',
            }}),
            'A weakening line that the file already contains must be allowed.',
        )
        self.__assert_allowed(
            self.__run_call({'name': 'edit', 'args': {
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
                self.__new_session()
                self.__assert_denied(
                    self.__run_call({'name': tool_name, 'args': args}),
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
                self.__assert_allowed(self.__run_call({'name': 'edit', 'args': args}), name)
        self.__assert_allowed(
            self.__run_call({'name': 'create', 'args': {'path': 'test_new.py', 'file_text': 'def test_a():\n    assert a()\n'}}),
            'A new test file must be allowed.',
        )

    def test_denies_only_once(self):
        call = {'name': 'edit', 'args': {'path': 'test_math.py', 'old_str': 'assert sub(3, 1) == 2', 'new_str': 'pass'}}
        self.__assert_denied(self.__run_call(call), ['retry the same edit'], 'first')
        self.__assert_allowed(self.__run_call(call), 'The same kind of denial must not repeat for the same file.')
        self.__assert_denied(
            self.__run_call({'name': 'insert', 'args': {'path': 'test_math.py', 'insert_line': 1, 'new_str': '@pytest.mark.skip\n'}}),
            ['@pytest.mark.skip'], 'A different kind must still be denied once.',
        )

    def test_patch_files_are_reported_together(self):
        result = self.__run_call({'name': 'apply_patch', 'args': (
            '*** Begin Patch\n*** Update File: test_math.py\n@@\n+@pytest.mark.skip\n'
            '*** Add File: src/app.test.ts\n+it.only("a", () => {});\n*** End Patch\n'
        )})
        self.__assert_denied(result, ['test_math.py', 'src/app.test.ts'], 'patch')

    def test_view_and_other_tools_are_ignored(self):
        for tool_call in (
            {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'test_math.py'}},
            {'name': 'bash', 'args': {'command': 'echo "@pytest.mark.skip" >> test_math.py'}},
        ):
            with self.subTest(tool_call=tool_call):
                self.__assert_allowed(self.__run_call(tool_call), 'Non-writing tool calls must be ignored.')

    def test_invalid_input(self):
        def encode(value):
            return json.dumps(value).encode('utf-8')

        call = {'toolName': 'edit', 'toolArgs': {}}
        for name, hook_input in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('no session', encode({'cwd': self.__directory, **call})),
            ('invalid session', encode({'sessionId': 'x', 'cwd': self.__directory, **call})),
            ('no cwd', encode({'sessionId': self.__session_id, **call})),
            ('relative cwd', encode({'sessionId': self.__session_id, 'cwd': 'relative', **call})),
            ('no tool args', encode({'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit'})),
            ('tool name not string', encode({'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 1, 'toolArgs': {}})),
            ('no tool name', encode({'sessionId': self.__session_id, 'cwd': self.__directory, 'toolArgs': {}})),
            ('invalid utf-8', b'\xff'),
        ]:
            with self.subTest(name=name):
                result = _run_hook(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check test weakening:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = json.dumps({
            'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {},
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
                path = os.path.join(self.__directory, f'definitions-{name.replace(" ", "-")}.json')
                with open(path, 'w', encoding='utf-8') as file:
                    file.write(content if isinstance(content, str) else json.dumps(content))
                result = _run_hook(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check test weakening:', result.stderr)


if __name__ == '__main__':
    run_tests()
