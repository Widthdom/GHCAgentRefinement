import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'block-elided-rewrites.py')
state_directory = os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-elided-rewrites')

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

ORIGINAL = ''.join(f'line {number}\n' for number in range(1, 41))


def run_hook(input_bytes, *arguments):
    return subprocess.run([sys.executable, hook_path, *arguments], input=input_bytes, capture_output=True)


class BlockElidedRewritesTests(unittest.TestCase):
    def setUp(self):
        self.directory = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.new_session()
        with open(os.path.join(self.directory, 'app.py'), 'w', encoding='utf-8', newline='\n') as file:
            file.write(ORIGINAL)

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

    def test_elision_comments_are_denied(self):
        placeholders = [
            '# ...',
            '    // ...',
            '/* … */',
            '<!-- ... -->',
            '-- ...',
            '# ... existing code ...',
            '// rest of the file unchanged',
            '    # ... rest of implementation',
            '// (remaining methods omitted)',
            '# existing imports',
            '// code remains unchanged',
            '# same as before',
            '/* omitted for brevity */',
            ' * ... other functions ...',
            '# 既存のコード',
            '// ... 以下同じ ...',
            '# 残りは変更なし',
            '# (省略)',
            '// 中略',
        ]
        for placeholder in placeholders:
            with self.subTest(placeholder=placeholder):
                self.new_session()
                self.assert_denied(
                    self.run_calls({'name': 'edit', 'args': {
                        'path': 'app.py', 'old_str': 'line 2\n', 'new_str': f'line 2 changed\n{placeholder}\n',
                    }}),
                    ['app.py', placeholder.strip()], placeholder,
                )

    def test_normal_comments_are_allowed(self):
        comments = [
            '# Keep the existing behavior for callers.',
            '# 省略可能な引数',
            '# 既存ファイルを上書きしない',
            '// TODO: handle errors',
            '...',
            'print("...")',
            'x = items[...]',
            '# See the rest of this module for details.',
        ]
        for comment in comments:
            with self.subTest(comment=comment):
                self.assert_allowed(
                    self.run_calls({'name': 'edit', 'args': {
                        'path': 'app.py', 'old_str': 'line 2\n', 'new_str': f'line 2\n{comment}\n',
                    }}),
                    comment,
                )

    def test_tool_argument_shapes(self):
        cases = [
            ('functions.edit', {'path': 'app.py', 'old_str': 'line 2\n', 'new_str': '# ...\n'}),
            ('insert', {'path': 'app.py', 'insert_line': 1, 'new_str': '# ...\n'}),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'app.py', 'old_str': 'line 2', 'new_str': '# ...'}),
            ('edit', json.dumps({'path': 'app.py', 'old_str': 'line 2\n', 'new_str': '# ...\n'})),
            ('apply_patch', '*** Begin Patch\n*** Update File: app.py\n@@\n-line 2\n+# ...\n*** End Patch\n'),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name):
                self.new_session()
                self.assert_denied(self.run_calls({'name': tool_name, 'args': args}), ['app.py', '"# ..."'], tool_name)

    def test_existing_placeholder_is_allowed(self):
        self.assert_allowed(
            self.run_calls({'name': 'edit', 'args': {
                'path': 'app.py', 'old_str': 'a\n# ... rest of the file\n', 'new_str': 'b\n# ... rest of the file\n',
            }}),
            'A placeholder that the replaced text already contains must be allowed.',
        )
        with open(os.path.join(self.directory, 'doc.md'), 'w', encoding='utf-8') as file:
            file.write('<!-- ... -->\n')
        self.assert_allowed(
            self.run_calls({'name': 'insert', 'args': {'path': 'doc.md', 'insert_line': 1, 'new_str': '<!-- ... -->'}}),
            'A placeholder that the file already contains must be allowed.',
        )

    def test_new_files_are_allowed(self):
        self.assert_allowed(
            self.run_calls(
                {'name': 'create', 'args': {'path': 'new.py', 'file_text': 'def main():\n    # ... rest of implementation\n'}},
                {'name': 'apply_patch', 'args': '*** Begin Patch\n*** Add File: other.py\n+# ...\n*** End Patch\n'},
            ),
            'Placeholders in new files must be allowed.',
        )

    def test_shrinking_overwrite(self):
        self.assert_denied(
            self.run_calls({'name': 'create', 'args': {'path': 'app.py', 'file_text': 'line 1\nline 2\n'}}),
            ['app.py would be overwritten with 2 lines although it currently has 40 lines.'], 'shrink',
        )
        self.new_session()
        self.assert_denied(
            self.run_calls({'name': 'str_replace_editor', 'args': {'command': 'create', 'path': 'app.py', 'file_text': 'x\n'}}),
            ['has 40 lines'], 'str_replace_editor create',
        )

    def test_non_shrinking_overwrite_is_allowed(self):
        with open(os.path.join(self.directory, 'small.py'), 'w', encoding='utf-8') as file:
            file.write('a\n' * (19))
        cases = [
            ('half or more', 'app.py', 'x\n' * 20),
            ('small original', 'small.py', 'x\n'),
        ]
        for name, path, content in cases:
            with self.subTest(name=name):
                self.assert_allowed(self.run_calls({'name': 'create', 'args': {'path': path, 'file_text': content}}), name)

    def test_denies_only_once(self):
        call = {'name': 'edit', 'args': {'path': 'app.py', 'old_str': 'line 2\n', 'new_str': '# ...\n'}}
        self.assert_denied(self.run_calls(call), ['retry the same edit'], 'first')
        self.assert_allowed(self.run_calls(call), 'The same kind of denial must not repeat for the same file.')
        self.assert_denied(
            self.run_calls({'name': 'create', 'args': {'path': 'app.py', 'file_text': 'x\n'}}),
            ['has 40 lines'], 'A different kind must still be denied once.',
        )

    def test_view_and_other_tools_are_ignored(self):
        self.assert_allowed(
            self.run_calls(
                {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'app.py'}},
                {'name': 'bash', 'args': {'command': 'echo "# ..." > app.py'}},
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
                self.assertIn(b'Failed to check elided rewrites:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = json.dumps({
            'sessionId': self.session_id, 'cwd': self.directory, 'toolCalls': [{'name': 'edit', 'args': {}}],
        }).encode('utf-8')
        for name, content in [
            ('not json', '{'),
            ('empty array', '[]'),
            ('missing pattern', '[{"name": "x"}]'),
            ('invalid regex', '[{"name": "x", "pattern": "("}]'),
        ]:
            with self.subTest(name=name):
                path = os.path.join(self.directory, f'definitions-{name.replace(" ", "-")}.json')
                with open(path, 'w', encoding='utf-8') as file:
                    file.write(content)
                result = run_hook(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check elided rewrites:', result.stderr)


if __name__ == '__main__':
    run_tests()
