import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-mojibake-writes.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

# 文字化けは、テストのコード上にそのまま書かず、正しい文字列を誤った文字コードで復号して作る。
_JAPANESE = 'これは日本語のテストです'
_CP932_MOJIBAKE = _JAPANESE.encode('utf-8').decode('cp932', errors='replace')
_CP932_MOJIBAKE_WITHOUT_REPLACEMENT = 'ファイル'.encode('utf-8').decode('cp932')
_CP1252_MOJIBAKE = 'ファイル'.encode('utf-8').decode('cp1252', errors='ignore')
_LATIN1_MOJIBAKE = 'ファイル'.encode('utf-8').decode('latin-1')
_ACCENT_MOJIBAKE = 'café'.encode('utf-8').decode('cp1252')
_REPLACEMENT_CHARACTER = chr(0xFFFD)  # 置換文字（復号できなかったバイトの代わり）
_NO_BREAK_SPACE = chr(0x00A0)  # ノーブレークスペース


class BlockMojibakeWritesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-mojibake-writes-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.directory = os.path.join(self.root, 'workspace')
        os.makedirs(self.directory)
        self.write_file('app.py', 'value = 1\nname = "a"\n')
        self.new_session()

    def write_file(self, path, content):
        full_path = os.path.join(self.directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def new_session(self):
        # テストごとに別セッションとし、拒否の記録を持ち越さない。
        self.session_id = str(uuid.uuid4())

    def run_process(self, hook_input, *arguments):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=10,
        )

    def run_call(self, tool_call):
        return self.run_process({
            'sessionId': self.session_id, 'cwd': self.directory, 'toolName': tool_call['name'], 'toolArgs': tool_call['args'],
        })

    def assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        self.assertEqual(result.stderr.decode('utf-8').strip(), output['permissionDecisionReason'], message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)
        return output['permissionDecisionReason']

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'preToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/preToolUse/block-mojibake-writes/block-mojibake-writes.py'
        entries = configuration['hooks']['preToolUse']
        matching = [
            entry for entry in entries if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['timeoutSec'], 10)
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertRegex(tool_name, f'^(?:{matching[0]["matcher"]})$')
        self.assertNotRegex('bash', f'^(?:{matching[0]["matcher"]})$')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_mojibake_is_denied(self):
        cases = [
            ('a.py', f'message = "{_CP932_MOJIBAKE}"\n', 'UTF-8 read as Shift_JIS (CP932)'),
            ('a.py', f'message = "{_CP932_MOJIBAKE}"\n', 'replacement character (U+FFFD)'),
            ('a.py', f'# {_CP932_MOJIBAKE_WITHOUT_REPLACEMENT}\n', 'UTF-8 read as Shift_JIS (CP932)'),
            ('a.py', f'label = "{_CP1252_MOJIBAKE}"\n', 'UTF-8 read as Latin-1 or Windows-1252'),
            ('a.py', f'label = "{_LATIN1_MOJIBAKE}"\n', 'UTF-8 read as Latin-1 or Windows-1252'),
            ('a.py', f'name = "{_ACCENT_MOJIBAKE}"\n', 'UTF-8 read as Latin-1 or Windows-1252'),
            ('a.py', f'name = "a{_REPLACEMENT_CHARACTER}b"\n', 'replacement character (U+FFFD)'),
            ('README.md', f'# {_CP932_MOJIBAKE_WITHOUT_REPLACEMENT}\n', 'UTF-8 read as Shift_JIS (CP932)'),
        ]
        for path, content, kind in cases:
            with self.subTest(kind=kind, content=content.encode('unicode_escape')):
                self.new_session()
                self.assert_denied(
                    self.run_call({'name': 'create', 'args': {'path': path, 'file_text': content}}),
                    [path, kind, 'Re-read the source as UTF-8', 'encode("utf-8").decode("cp932")',
                     'Retry this edit unchanged only if'], kind,
                )

    def test_control_characters_in_snippet_are_visible(self):
        reason = self.assert_denied(
            self.run_call({'name': 'create', 'args': {'path': 'a.py', 'file_text': f'x = "{_LATIN1_MOJIBAKE}"\n'}}),
            ['<U+0083>'], 'C1 control characters',
        )
        self.assertNotIn(chr(0x83), reason)

    def test_allowed_contents(self):
        cases = [
            ('a.py', f'message = "{_JAPANESE}"\n'),
            ('a.py', '# 縮小・繰り返し・編集・継続・総合・維持の漢字を含むコメント\n'),
            ('a.py', 'name = "café résumé naïve Ångström São Paulo"\n'),
            ('a.py', f'quote = "«{_NO_BREAK_SPACE}café{_NO_BREAK_SPACE}»"\n'),
            ('a.py', 'price = "€5 — “quoted” … ™"\n'),
            ('a.py', 'text = "\\ufffd is written as an escape"\n'),
            ('a.py', 'mojibake = "ファイル".encode("utf-8").decode("cp932")\n'),
            ('README.md', 'ｱｲｳｴｵ と 半角カナ、全角カナ アイウエオ\n'),
        ]
        for path, content in cases:
            with self.subTest(path=path, content=content.encode('unicode_escape')):
                self.assert_allowed(
                    self.run_call({'name': 'create', 'args': {'path': path, 'file_text': content}}), content,
                )

    def test_tool_formats(self):
        bad = f'value = "{_CP932_MOJIBAKE_WITHOUT_REPLACEMENT}"'
        cases = [
            ('edit', {'path': 'app.py', 'old_str': 'value = 1', 'new_str': bad}),
            ('functions.edit', json.dumps({'path': 'app.py', 'old_str': 'value = 1', 'new_str': bad})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'app.py', 'old_str': 'value = 1', 'new_str': bad}),
            ('apply_patch', f'*** Begin Patch\n*** Update File: app.py\n@@\n-value = 1\n+{bad}\n*** End Patch\n'),
            ('insert', {'path': 'app.py', 'insert_line': 1, 'new_str': f'{bad}\n'}),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name):
                self.new_session()
                self.assert_denied(self.run_call({'name': tool_name, 'args': args}), ['app.py', 'CP932'], tool_name)

    def test_existing_lines_are_allowed(self):
        line = f'name = "{_CP932_MOJIBAKE}"'
        self.write_file('legacy.py', f'{line}\nvalue = 1\n')
        self.assert_allowed(
            self.run_call({'name': 'edit', 'args': {'path': 'legacy.py', 'old_str': 'value = 1', 'new_str': 'value = 2'}}),
            'Mojibake that the file already has must be allowed.',
        )
        self.assert_allowed(
            self.run_call({'name': 'edit', 'args': {'path': 'legacy.py', 'old_str': line, 'new_str': f'    {line}'}}),
            'Reindenting an existing line must be allowed.',
        )

    def test_ignored_calls(self):
        for tool_call in (
            {'name': 'apply_patch', 'args': '*** Begin Patch\n*** Delete File: app.py\n*** End Patch\n'},
            {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'app.py'}},
            {'name': 'bash', 'args': {'command': f'echo "{_CP932_MOJIBAKE}" > a.py'}},
        ):
            with self.subTest(tool_call=tool_call['name']):
                self.assert_allowed(self.run_call(tool_call), 'Deletions and non-writing tools must be ignored.')

    def test_excluded_files_are_allowed(self):
        definitions_path = os.path.join(self.root, 'definitions.json')
        with open(os.path.join(_script_directory, 'mojibake-definitions.json'), encoding='utf-8') as file:
            definitions = json.load(file)
        definitions['excludedFilePatterns'] = ['(?i)^fixtures/']
        with open(definitions_path, 'w', encoding='utf-8') as file:
            json.dump(definitions, file)
        hook_input = {
            'sessionId': self.session_id, 'cwd': self.directory, 'toolName': 'create',
            'toolArgs': {'path': 'fixtures/broken.txt', 'file_text': _CP932_MOJIBAKE},
        }
        self.assert_allowed(self.run_process(hook_input, '--definitions-path', definitions_path), 'excluded')
        hook_input['toolArgs']['path'] = 'src/broken.txt'
        self.assert_denied(self.run_process(hook_input, '--definitions-path', definitions_path), ['src/broken.txt'], 'not excluded')

    def test_denies_only_once_per_file(self):
        call = {'name': 'create', 'args': {'path': 'a.py', 'file_text': f'x = "{_CP932_MOJIBAKE}"\n'}}
        self.assert_denied(self.run_call(call), ['a.py'], 'first')
        self.assert_allowed(self.run_call(call), 'The same file must not be denied twice.')
        self.assert_denied(
            self.run_call({'name': 'create', 'args': {'path': 'b.py', 'file_text': f'x = "{_CP932_MOJIBAKE}"\n'}}),
            ['b.py'], 'Another file must still be denied once.',
        )

    def test_patch_files_are_reported_together_and_snippets_are_limited(self):
        lines = ''.join(f'+line{index} = "{_CP932_MOJIBAKE_WITHOUT_REPLACEMENT}"\n' for index in range(5))
        result = self.run_call({'name': 'apply_patch', 'args': (
            f'*** Begin Patch\n*** Add File: a.py\n{lines}'
            f'*** Add File: b.ts\n+const x = "{_ACCENT_MOJIBAKE}";\n*** End Patch\n'
        )})
        reason = self.assert_denied(result, ['a.py', 'b.ts', 'line2 ', 'Latin-1'], 'patch')
        self.assertNotIn('line3 ', reason)

    def test_long_snippet_is_truncated(self):
        content = f'x = "{_CP932_MOJIBAKE_WITHOUT_REPLACEMENT}{"y" * 200}"\n'
        reason = self.assert_denied(
            self.run_call({'name': 'create', 'args': {'path': 'a.py', 'file_text': content}}), ['...'], 'long',
        )
        self.assertNotIn('y' * 100, reason)

    def test_invalid_input(self):
        def encode(value):
            return json.dumps(value).encode('utf-8')

        call = {'toolName': 'edit', 'toolArgs': {}}
        for name, hook_input in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('no session', encode({'cwd': self.directory, **call})),
            ('invalid session', encode({'sessionId': 'x', 'cwd': self.directory, **call})),
            ('no cwd', encode({'sessionId': self.session_id, **call})),
            ('relative cwd', encode({'sessionId': self.session_id, 'cwd': 'relative', **call})),
            ('no tool args', encode({'sessionId': self.session_id, 'cwd': self.directory, 'toolName': 'edit'})),
            ('tool name not string', encode({'sessionId': self.session_id, 'cwd': self.directory, 'toolName': 1, 'toolArgs': {}})),
            ('no tool name', encode({'sessionId': self.session_id, 'cwd': self.directory, 'toolArgs': {}})),
            ('invalid utf-8', b'\xff'),
        ]:
            with self.subTest(name=name):
                result = self.run_process(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check mojibake:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = {'sessionId': self.session_id, 'cwd': self.directory, 'toolName': 'edit', 'toolArgs': {}}
        valid_patterns = [{'name': 'x', 'pattern': 'x'}]
        for name, content in [
            ('missing file', None),
            ('not json', '{'),
            ('not object', '[]'),
            ('missing excluded patterns', {'mojibakePatterns': valid_patterns}),
            ('empty excluded pattern', {'excludedFilePatterns': [''], 'mojibakePatterns': valid_patterns}),
            ('empty mojibake patterns', {'excludedFilePatterns': [], 'mojibakePatterns': []}),
            ('missing pattern', {'excludedFilePatterns': [], 'mojibakePatterns': [{'name': 'x'}]}),
            ('invalid excluded regex', {'excludedFilePatterns': ['('], 'mojibakePatterns': valid_patterns}),
            ('invalid mojibake regex', {'excludedFilePatterns': [], 'mojibakePatterns': [{'name': 'x', 'pattern': '('}]}),
        ]:
            with self.subTest(name=name):
                path = os.path.join(self.root, f'definitions-{name.replace(" ", "-")}.json')
                if content is not None:
                    with open(path, 'w', encoding='utf-8') as file:
                        file.write(content if isinstance(content, str) else json.dumps(content))
                result = self.run_process(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check mojibake:', result.stderr)


if __name__ == '__main__':
    run_tests()
