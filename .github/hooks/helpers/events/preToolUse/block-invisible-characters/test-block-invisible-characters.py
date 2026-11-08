import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-invisible-characters.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_get_hook_entry = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-entry.py'))['get_hook_entry']

# 見えない文字は、テストのコード上で見えるようにchrで書く。
_ZERO_WIDTH_SPACE = chr(0x200B)  # ゼロ幅スペース
_BOM = chr(0xFEFF)  # バイトオーダーマーク・ゼロ幅ノーブレークスペース
_RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)  # 右から左への上書き
_SOFT_HYPHEN = chr(0x00AD)  # ソフトハイフン
_NO_BREAK_SPACE = chr(0x00A0)  # ノーブレークスペース
_IDEOGRAPHIC_SPACE = chr(0x3000)  # 全角スペース


class BlockInvisibleCharactersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-invisible-characters-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__directory = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__directory)
        self.__write_file('app.py', 'value = 1\nname = "a"\n')
        self.__new_session()

    def __write_file(self, path, content):
        full_path = os.path.join(self.__directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __new_session(self):
        # テストごとに別セッションとし、拒否の記録を持ち越さない。
        self.__session_id = str(uuid.uuid4())

    def __run_process(self, hook_input, *arguments):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __run_call(self, tool_call):
        return self.__run_process({
            'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': tool_call['name'], 'toolArgs': tool_call['args'],
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
        return output['permissionDecisionReason']

    def test_configuration(self):
        entry = _get_hook_entry(_hook_path)
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertRegex(tool_name, f'^(?:{entry["matcher"]})$')
        self.assertNotRegex('bash', f'^(?:{entry["matcher"]})$')

    def test_invisible_characters_are_denied(self):
        cases = [
            (f'name = "a{_ZERO_WIDTH_SPACE}b"\n', 'zero-width character', '<U+200B>'),
            (f'{_BOM}import os\n', 'zero-width character', '<U+FEFF>'),
            (f'if role != "user{_RIGHT_TO_LEFT_OVERRIDE}":\n', 'bidirectional control character', '<U+202E>'),
            (f'total = sub{_SOFT_HYPHEN}total\n', 'invisible formatting character', '<U+00AD>'),
            (f'value ={_NO_BREAK_SPACE}1\n', 'no-break space', '<U+00A0>'),
            (f'{_IDEOGRAPHIC_SPACE}value = 1\n', 'ideographic space in code', '<U+3000>'),
            (f'value = 1{_IDEOGRAPHIC_SPACE}\n', 'ideographic space in code', '<U+3000>'),
            (f'value{_IDEOGRAPHIC_SPACE}= 1\n', 'ideographic space in code', '<U+3000>'),
        ]
        for content, kind, visible in cases:
            with self.subTest(kind=kind, content=content.encode('unicode_escape')):
                self.__new_session()
                self.__assert_denied(
                    self.__run_call({'name': 'create', 'args': {'path': 'a.py', 'file_text': content}}),
                    [
                        'a.py', kind, visible, 'chr(0x3000)', 'read the file after the edit',
                        'Retry this edit unchanged only if',
                    ], kind,
                )

    def test_allowed_contents(self):
        cases = [
            ('a.py', 'value = 1  # 普通の空白\n'),
            ('a.py', f'# 日本語の文{_IDEOGRAPHIC_SPACE}中の全角スペース\n'),
            ('a.py', 'message = "\\u3000 and \\u200b are written as escapes"\n'),
            ('README.md', f'Text{_NO_BREAK_SPACE}with a no-break space and a{_ZERO_WIDTH_SPACE}zero-width space.\n'),
            ('notes.txt', f'{_IDEOGRAPHIC_SPACE}indented\n'),
        ]
        for path, content in cases:
            with self.subTest(path=path, content=content.encode('unicode_escape')):
                self.__assert_allowed(
                    self.__run_call({'name': 'create', 'args': {'path': path, 'file_text': content}}), content,
                )

    def test_tool_formats(self):
        bad = f'value = {_ZERO_WIDTH_SPACE}2'
        cases = [
            ('edit', {'path': 'app.py', 'old_str': 'value = 1', 'new_str': bad}),
            ('functions.edit', json.dumps({'path': 'app.py', 'old_str': 'value = 1', 'new_str': bad})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'app.py', 'old_str': 'value = 1', 'new_str': bad}),
            ('apply_patch', f'*** Begin Patch\n*** Update File: app.py\n@@\n-value = 1\n+{bad}\n*** End Patch\n'),
            ('insert', {'path': 'app.py', 'insert_line': 1, 'new_str': f'{bad}\n'}),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name):
                self.__new_session()
                self.__assert_denied(self.__run_call({'name': tool_name, 'args': args}), ['app.py', '<U+200B>'], tool_name)

    def test_existing_lines_are_allowed(self):
        self.__write_file('legacy.py', f'name = "a{_ZERO_WIDTH_SPACE}b"\nvalue = 1\n')
        self.__assert_allowed(
            self.__run_call({'name': 'edit', 'args': {
                'path': 'legacy.py', 'old_str': 'value = 1', 'new_str': 'value = 2',
            }}),
            'Invisible characters that the file already has must be allowed.',
        )
        self.__assert_allowed(
            self.__run_call({'name': 'edit', 'args': {
                'path': 'legacy.py', 'old_str': f'name = "a{_ZERO_WIDTH_SPACE}b"', 'new_str': f'    name = "a{_ZERO_WIDTH_SPACE}b"',
            }}),
            'Reindenting an existing line must be allowed.',
        )

    def test_adding_ideographic_indent_to_existing_line_is_denied(self):
        # 行頭の全角スペースは前後の空白として除かず、既存の行とは別の行とみなす。
        self.__assert_denied(
            self.__run_call({'name': 'edit', 'args': {
                'path': 'app.py', 'old_str': 'value = 1', 'new_str': f'{_IDEOGRAPHIC_SPACE}value = 1',
            }}),
            ['ideographic space in code'], 'ideographic indent',
        )

    def test_ignored_calls(self):
        for tool_call in (
            {'name': 'apply_patch', 'args': '*** Begin Patch\n*** Delete File: app.py\n*** End Patch\n'},
            {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'app.py'}},
            {'name': 'bash', 'args': {'command': f'echo "{_ZERO_WIDTH_SPACE}" > a.py'}},
        ):
            with self.subTest(tool_call=tool_call['name']):
                self.__assert_allowed(self.__run_call(tool_call), 'Deletions and non-writing tools must be ignored.')

    def test_denies_only_once_per_file(self):
        call = {'name': 'create', 'args': {'path': 'a.py', 'file_text': f'x = {_ZERO_WIDTH_SPACE}1\n'}}
        self.__assert_denied(self.__run_call(call), ['a.py'], 'first')
        self.__assert_allowed(self.__run_call(call), 'The same file must not be denied twice.')
        self.__assert_denied(
            self.__run_call({'name': 'create', 'args': {'path': 'b.py', 'file_text': f'x = {_ZERO_WIDTH_SPACE}1\n'}}),
            ['b.py'], 'Another file must still be denied once.',
        )

    def test_patch_files_are_reported_together_and_snippets_are_limited(self):
        lines = ''.join(f'+line{index} = {_ZERO_WIDTH_SPACE}{index}\n' for index in range(5))
        result = self.__run_call({'name': 'apply_patch', 'args': (
            f'*** Begin Patch\n*** Add File: a.py\n{lines}'
            f'*** Add File: b.ts\n+const x ={_NO_BREAK_SPACE}1;\n*** End Patch\n'
        )})
        reason = self.__assert_denied(result, ['a.py', 'b.ts', 'line2 ', 'no-break space'], 'patch')
        self.assertNotIn('line3 ', reason)

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
                result = self.__run_process(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check invisible characters:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}}
        valid_patterns = [{'name': 'x', 'pattern': 'x'}]
        for name, content in [
            ('missing file', None),
            ('not json', '{'),
            ('not object', '[]'),
            ('missing excluded patterns', {'characterPatterns': valid_patterns}),
            ('empty character patterns', {'excludedFilePatterns': [], 'characterPatterns': []}),
            ('missing pattern', {'excludedFilePatterns': [], 'characterPatterns': [{'name': 'x'}]}),
            ('invalid excluded regex', {'excludedFilePatterns': ['('], 'characterPatterns': valid_patterns}),
            ('invalid character regex', {'excludedFilePatterns': [], 'characterPatterns': [{'name': 'x', 'pattern': '('}]}),
        ]:
            with self.subTest(name=name):
                path = os.path.join(self.__root, f'definitions-{name.replace(" ", "-")}.json')
                if content is not None:
                    with open(path, 'w', encoding='utf-8', newline='\n') as file:
                        file.write(content if isinstance(content, str) else json.dumps(content))
                result = self.__run_process(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check invisible characters:', result.stderr)


if __name__ == '__main__':
    run_tests()
