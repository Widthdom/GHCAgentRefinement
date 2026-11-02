import json
import os
import subprocess
import sys
import tempfile
import unittest


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'block-py314-incompatible-syntax.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

ORIGINAL = (
    'def show(value):\n'
    '    return value\n'
    '\n'
    'print(show(1))\n'
)
# Python 3.14で追加された文法（PEP 750のテンプレート文字列、PEP 758の括弧なしの複数例外）。
PY314_SOURCES = [
    'name = "x"\ntemplate = t"Hello {name}"\n',
    'try:\n    pass\nexcept ValueError, TypeError:\n    pass\n',
]
INCOMPATIBLE_SOURCES = [
    ('Python 2 print', 'print "hello"\n'),
    ('Python 2 raise', 'raise ValueError, "message"\n'),
    ('Python 2 exec', 'exec "x = 1"\n'),
    ('Python 2 octal', 'x = 0777\n'),
    ('Python 2 backticks', 'x = `1`\n'),
    ('Python 2 <>', 'x = 1 <> 2\n'),
    ('return outside function', 'return 1\n'),
    ('unclosed parenthesis', 'print(1\n'),
]


@unittest.skipIf(sys.version_info < (3, 14), 'Python 3.14 or later is required to judge Python 3.14 syntax.')
class BlockPy314IncompatibleSyntaxTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-py314-incompatible-syntax-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = os.path.realpath(temporary.name)
        self.write_file('app.py', ORIGINAL)

    def write_file(self, path, content):
        full_path = os.path.join(self.directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def run_process(self, hook_input):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )

    def run_call(self, tool_name, tool_args):
        return self.run_process({'cwd': self.directory, 'toolName': tool_name, 'toolArgs': tool_args})

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
        with open(os.path.join(hooks_directory, '-repo-preToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = (
            '.github/hooks/helpers/-repo-events/preToolUse/block-py314-incompatible-syntax/'
            'block-py314-incompatible-syntax.py'
        )
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

    def test_incompatible_syntax_is_denied(self):
        for name, source in INCOMPATIBLE_SOURCES:
            with self.subTest(name=name):
                self.assert_denied(
                    self.run_call('create', {'path': 'new.py', 'file_text': source}), ['new.py line 1'], name,
                )

    def test_python_314_syntax_is_allowed(self):
        for source in [ORIGINAL, *PY314_SOURCES]:
            with self.subTest(source=source):
                self.assert_allowed(
                    self.run_call('create', {'path': 'new.py', 'file_text': source}), 'Python 3.14 syntax must be allowed.',
                )

    def test_tool_formats(self):
        broken = 'print "hello"'
        cases = [
            ('create', {'path': 'a.py', 'file_text': f'{broken}\n'}, 'a.py line 1'),
            ('create', {'path': 'a.pyi', 'file_text': f'{broken}\n'}, 'a.pyi line 1'),
            ('create', {'path': 'sub/A.PYW', 'file_text': f'{broken}\n'}, 'sub/A.PYW line 1'),
            ('edit', {'path': 'app.py', 'old_str': 'print(show(1))', 'new_str': broken}, 'app.py line 4'),
            ('functions.edit', json.dumps({'path': 'app.py', 'old_str': 'print(show(1))', 'new_str': broken}),
             'app.py line 4'),
            ('insert', {'path': 'app.py', 'insert_line': 0, 'new_str': broken}, 'app.py line 1'),
            ('insert', {'path': 'app.py', 'insert_line': 4, 'new_str': broken}, 'app.py line 5'),
            ('str_replace_editor', {
                'command': 'str_replace', 'path': 'app.py', 'old_str': 'print(show(1))', 'new_str': broken,
            }, 'app.py line 4'),
            ('str_replace_editor', {'command': 'insert', 'path': 'app.py', 'insert_line': 2, 'new_str': broken},
             'app.py line 3'),
            ('str_replace_editor', {'command': 'create', 'path': 'a.py', 'file_text': broken}, 'a.py line 1'),
            ('apply_patch', f'*** Begin Patch\n*** Add File: a.py\n+{broken}\n*** End Patch\n', 'a.py line 1'),
            ('apply_patch', (
                '*** Begin Patch\n*** Update File: app.py\n@@ def show(value):\n'
                '-print(show(1))\n+' + broken + '\n*** End Patch\n'
            ), 'app.py line 4'),
            ('apply_patch', (
                '*** Begin Patch\n*** Update File: app.py\n*** Move to: moved.py\n@@\n'
                ' \n-print(show(1))\n+' + broken + '\n*** End Patch\n'
            ), 'moved.py line 4'),
            ('apply_patch', {'input': f'*** Begin Patch\n*** Add File: a.py\n+{broken}\n*** End Patch\n'}, 'a.py line 1'),
        ]
        for tool_name, args, part in cases:
            with self.subTest(tool_name=tool_name, args=args):
                self.assert_denied(self.run_call(tool_name, args), [part], tool_name)

    def test_compatible_edits_are_allowed(self):
        cases = [
            ('edit', {'path': 'app.py', 'old_str': 'print(show(1))', 'new_str': 'print(show(2))'}),
            ('insert', {'path': 'app.py', 'insert_line': 4, 'new_str': 'print(t"{show(1)}")'}),
            ('apply_patch', (
                '*** Begin Patch\n*** Update File: app.py\n@@\n-print(show(1))\n+print(show(2))\n'
                '*** Add File: b.py\n+x = 1\n*** End Patch\n'
            )),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name):
                self.assert_allowed(self.run_call(tool_name, args), 'Compatible edits must be allowed.')

    def test_patch_files_are_reported_together(self):
        result = self.run_call('apply_patch', (
            '*** Begin Patch\n*** Add File: a.py\n+print "a"\n'
            '*** Add File: b.py\n+x = 0777\n*** End Patch\n'
        ))
        self.assert_denied(result, ['a.py line 1', 'b.py line 1'], 'patch')

    def test_skipped_edits(self):
        self.write_file('broken.py', 'print "already broken"\n')
        cases = [
            ('not python', 'create', {'path': 'a.txt', 'file_text': 'print "hello"\n'}),
            ('python in document', 'create', {'path': 'README.md', 'file_text': 'print "hello"\n'}),
            ('already broken', 'edit', {'path': 'broken.py', 'old_str': 'already', 'new_str': 'still'}),
            ('old_str not found', 'edit', {'path': 'app.py', 'old_str': 'missing', 'new_str': 'print "x"'}),
            ('missing file', 'edit', {'path': 'missing.py', 'old_str': 'a', 'new_str': 'print "x"'}),
            ('insert out of range', 'insert', {'path': 'app.py', 'insert_line': 99, 'new_str': 'print "x"'}),
            ('view', 'str_replace_editor', {'command': 'view', 'path': 'app.py'}),
            ('patch context not found', 'apply_patch', (
                '*** Begin Patch\n*** Update File: app.py\n@@\n-missing\n+print "x"\n*** End Patch\n'
            )),
            ('patch delete', 'apply_patch', '*** Begin Patch\n*** Delete File: app.py\n*** End Patch\n'),
            ('shell', 'bash', {'command': 'echo \'print "x"\' > a.py'}),
        ]
        for name, tool_name, args in cases:
            with self.subTest(name=name):
                self.assert_allowed(self.run_call(tool_name, args), f'{name} must be skipped.')

    def test_invalid_input(self):
        def encode(value):
            return json.dumps(value).encode('utf-8')

        call = {'toolName': 'edit', 'toolArgs': {}}
        for name, hook_input in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('not utf-8', b'\xff'),
            ('no cwd', encode(call)),
            ('relative cwd', encode({'cwd': 'relative', **call})),
            ('no tool name', encode({'cwd': self.directory, 'toolArgs': {}})),
            ('no tool args', encode({'cwd': self.directory, 'toolName': 'edit'})),
        ]:
            with self.subTest(name=name):
                result = self.run_process(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check Python 3.14 syntax', result.stderr, name)


if __name__ == '__main__':
    run_tests()
