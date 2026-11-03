import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-hardcoding-for-tests.py')
_recorder_path = os.path.join(
    os.path.dirname(os.path.dirname(_script_directory)), 'postToolUse', 'record-test-failure-values',
    'record-test-failure-values.py',
)
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_IMPLEMENTATION = 'def add(a, b):\n    return a - b\n'
_SPECIAL_CASE = 'def add(a, b):\n    if a == 1 and b == 2:\n        return 4\n    return a - b\n'


class BlockHardcodingForTestsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-hardcoding-for-tests-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__directory = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__directory)
        self.__write_file('math.py', _IMPLEMENTATION)
        self.__write_file('test_math.py', 'def test_add():\n    assert add(1, 2) == 4\n')
        self.__new_session()

    def __write_file(self, path, content):
        full_path = os.path.join(self.__directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __new_session(self):
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__root, 'ghc-agent-refinement', 'hook-states', 'test-failure-values', f'{self.__session_id}.sqlite3',
        )

    def __run_process(self, path, hook_input, *arguments):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __record_failure(self, output, command='npx jest'):
        # 記録側のhookを実際に通し、状態の形式の食い違いも検出する。
        result = self.__run_process(_recorder_path, {
            'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'bash',
            'toolArgs': {'command': command},
            'toolResult': {'resultType': 'success', 'textResultForLlm': output},
        })
        self.assertEqual((result.returncode, result.stderr), (0, b''))

    def __run_call(self, tool_call):
        return self.__run_process(
            _hook_path, {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': tool_call['name'], 'toolArgs': tool_call['args']},
        )

    def __assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'preToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/preToolUse/block-hardcoding-for-tests/block-hardcoding-for-tests.py'
        entries = [
            entry for entry in configuration['hooks']['preToolUse']
            if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        self.assertNotRegex('bash', f'^(?:{entries[0]["matcher"]})$')

    def test_hardcoding_with_each_tool_is_denied(self):
        cases = [
            ('edit', {'path': 'math.py', 'old_str': '    return a - b\n',
                      'new_str': '    if a == 1 and b == 2:\n        return 4\n    return a - b\n'}),
            ('functions.edit', json.dumps({'path': 'math.py', 'old_str': '    return a - b',
                                           'new_str': '    if a == 1: return 4\n    return a - b'})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'math.py', 'old_str': '    return a - b',
                                    'new_str': '    if a == 1 and b == 2:\n        return 4\n    return a - b'}),
            ('insert', {'path': 'math.py', 'insert_line': 1, 'new_str': '    if a == 1:\n        return 4\n'}),
            ('create', {'path': 'math.py', 'file_text': _SPECIAL_CASE}),
            ('create new file', {'path': 'src/new_math.py', 'file_text': _SPECIAL_CASE}),
            ('apply_patch', '*** Begin Patch\n*** Update File: math.py\n@@\n def add(a, b):\n'
                            '+    if a == 1 and b == 2:\n+        return 4\n     return a - b\n*** End Patch\n'),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                self.__new_session()
                self.__record_failure('Expected: 4\nReceived: -1\n')
                tool_name = 'create' if name == 'create new file' else name
                self.__assert_denied(
                    self.__run_call({'name': tool_name, 'args': args}),
                    ['would add a branch on 1 that yields the expected value 4', 'retry the same edit'], name,
                )

    def test_branch_syntaxes_are_detected(self):
        cases = [
            ('c-like inline', 'if (a === 1 && b === 2) return "three";'),
            ('c-like block', 'if (a == 1) {\n    return "three";\n}'),
            ('else if', '} else if (name == "x") {\n  result = "three";'),
            ('switch case', 'case "x": return "three";'),
            ('python case', 'case 1:\n    return "three"'),
            ('ruby when', 'when 1 then "three"'),
            ('ternary', 'return a == 1 ? "three" : a + b;'),
            ('quotes differ', "if a == 1: return 'three'"),
        ]
        for name, new_text in cases:
            with self.subTest(name=name):
                self.__new_session()
                self.__record_failure('Expected: "three"\nReceived: "two"\n')
                self.__assert_denied(
                    self.__run_call({'name': 'edit', 'args': {'path': 'math.py', 'old_str': 'return a - b',
                                                             'new_str': new_text}}),
                    ['math.py', 'three'], name,
                )

    def test_symmetric_formats_use_both_values(self):
        self.__record_failure('E       assert -1 == 4\n', command='pytest')
        self.__assert_denied(
            self.__run_call({'name': 'edit', 'args': {'path': 'math.py', 'old_str': 'return a - b',
                                                     'new_str': 'if a == 1: return 4\n    return a - b'}}),
            ['math.py'], 'pytest does not tell which value is expected.',
        )

    def test_general_fixes_are_allowed(self):
        self.__record_failure('Expected: 4\nReceived: -1\n')
        cases = [
            ('general fix', {'path': 'math.py', 'old_str': 'return a - b', 'new_str': 'return a + b'}),
            ('constant fix', {'path': 'math.py', 'old_str': 'return 3', 'new_str': 'return 4'}),
            ('condition without literal', {'path': 'math.py', 'old_str': 'return a - b',
                                           'new_str': 'if a > b:\n        return a + b\n    return 4'}),
            ('branch with other value', {'path': 'math.py', 'old_str': 'return a - b',
                                         'new_str': 'if a == 1:\n        return 5\n    return a + b'}),
            ('expected value only in the condition', {'path': 'math.py', 'old_str': 'return a - b',
                                                      'new_str': 'if a == 4:\n        return a + b'}),
            ('value after the branch ends', {'path': 'math.py', 'old_str': 'return a - b',
                                             'new_str': 'if a == 1:\n        return 5\n    else:\n        return 4'}),
            ('number inside a name', {'path': 'math.py', 'old_str': 'return a - b',
                                      'new_str': 'if a == 1:\n        return value4'}),
            ('existing branch', {'path': 'math.py', 'old_str': '    if a == 1:\n        return 4\n    return a - b',
                                 'new_str': '    if a == 1:\n        return 4\n    return a + b'}),
            ('test file', {'path': 'test_math.py', 'old_str': 'assert add(1, 2) == 4',
                           'new_str': 'if x == 1:\n        return 4'}),
            ('document', {'path': 'README.md', 'old_str': 'x', 'new_str': 'if a == 1:\n    return 4'}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                self.__assert_allowed(self.__run_call({'name': 'edit', 'args': args}), name)

    def test_no_recorded_failure_is_allowed(self):
        call = {'name': 'create', 'args': {'path': 'math.py', 'file_text': _SPECIAL_CASE}}
        self.__assert_allowed(self.__run_call(call), 'No state file.')
        self.assertFalse(os.path.exists(self.__state_path), 'The check must not create a state file.')
        self.__record_failure('Expected: 4\nReceived: -1\n')
        self.__record_failure('1 passed\n')
        self.__assert_allowed(self.__run_call(call), 'A passing run clears the recorded failure.')

    def test_denies_only_once_per_file(self):
        self.__record_failure('Expected: 4\nReceived: -1\n')
        call = {'name': 'create', 'args': {'path': 'math.py', 'file_text': _SPECIAL_CASE}}
        self.__assert_denied(self.__run_call(call), ['math.py'], 'first')
        self.__assert_allowed(self.__run_call(call), 'The same file must not be denied twice.')
        self.__assert_denied(
            self.__run_call({'name': 'create', 'args': {'path': 'other.py', 'file_text': _SPECIAL_CASE}}),
            ['other.py'], 'Another file must still be denied once.',
        )

    def test_shares_state_with_goalpost_moving_without_conflict(self):
        # block-goalpost-movingの拒否済み記録とは別に数える。
        self.__record_failure('Expected: 4\nReceived: -1\n')
        goalpost_path = os.path.join(
            os.path.dirname(_script_directory), 'block-goalpost-moving', 'block-goalpost-moving.py',
        )
        result = self.__run_process(goalpost_path, {
            'sessionId': self.__session_id, 'cwd': self.__directory,
            'toolName': 'edit', 'toolArgs': {'path': 'math.py', 'old_str': 'return 4', 'new_str': 'return -1'},
        })
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))
        self.__assert_denied(
            self.__run_call({'name': 'create', 'args': {'path': 'math.py', 'file_text': _SPECIAL_CASE}}),
            ['math.py'], 'The state shared with block-goalpost-moving must still work.',
        )

    def test_patch_files_are_reported_together(self):
        self.__record_failure('Expected: 4\nReceived: -1\n')
        result = self.__run_call({'name': 'apply_patch', 'args': (
            '*** Begin Patch\n*** Add File: math.py\n'
            + ''.join(f'+{line}\n' for line in _SPECIAL_CASE.splitlines())
            + '*** Add File: src/calc.ts\n+if (a === 1) return 4;\n*** End Patch\n'
        )})
        self.__assert_denied(result, ['math.py', 'calc.ts'], 'patch')

    def test_other_tools_are_ignored(self):
        self.__record_failure('Expected: 4\nReceived: -1\n')
        for tool_call in (
            {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'math.py'}},
            {'name': 'bash', 'args': {'command': 'echo "if a == 1: return 4" >> math.py'}},
            {'name': 'apply_patch', 'args': '*** Begin Patch\n*** Delete File: math.py\n*** End Patch\n'},
        ):
            with self.subTest(tool_call=tool_call):
                self.__assert_allowed(self.__run_call(tool_call), 'Non-writing tools, shell commands, and deletions must be ignored.')

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
                result = self.__run_process(_hook_path, hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check hardcoding for tests:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}}
        for name, content in [
            ('missing file', None),
            ('not json', '{'),
            ('not object', '[]'),
            ('missing test file patterns', {'assertionPatterns': ['x']}),
            ('empty test file patterns', {'testFilePatterns': []}),
            ('invalid regex', {'testFilePatterns': ['(']}),
        ]:
            with self.subTest(name=name):
                path = os.path.join(self.__root, f'definitions-{name.replace(" ", "-")}.json')
                if content is not None:
                    with open(path, 'w', encoding='utf-8') as file:
                        file.write(content if isinstance(content, str) else json.dumps(content))
                result = self.__run_process(_hook_path, valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check hardcoding for tests:', result.stderr)


if __name__ == '__main__':
    run_tests()
