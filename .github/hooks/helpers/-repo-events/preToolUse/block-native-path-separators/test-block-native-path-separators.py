import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'block-native-path-separators.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

ORIGINAL = (
    'def show(path, cwd):\n'
    '    return os.path.relpath(path, cwd)\n'
    '\n'
    'print(show(a, b))\n'
)


class BlockNativePathSeparatorsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-native-path-separators-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.directory = os.path.join(self.root, 'workspace')
        os.makedirs(self.directory)
        self.write_file('app.py', ORIGINAL)
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
            [sys.executable, hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=10,
        )

    def run_call(self, tool_call):
        return self.run_process({'sessionId': self.session_id, 'cwd': self.directory, 'toolName': tool_call['name'], 'toolArgs': tool_call['args']})

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
            '.github/hooks/helpers/-repo-events/preToolUse/block-native-path-separators/block-native-path-separators.py'
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

    def test_native_paths_are_denied(self):
        cases = [
            ('a.py', 'return os.path.relpath(path, cwd)\n', 'Python relative path'),
            ('a.py', 'name = str(path.relative_to(root))\n', 'Python relative path'),
            ('a.ts', 'const rel = path.relative(root, file);\n', 'Node.js relative path'),
            ('A.cs', 'var rel = Path.GetRelativePath(root, file);\n', '.NET relative path'),
            ('a.ps1', '$rel = [System.IO.Path]::GetRelativePath($root, $file)\n', '.NET relative path'),
            ('a.ps1', '$rel = Resolve-Path $file -Relative\n', 'PowerShell relative path'),
            ('a.go', 'rel, err := filepath.Rel(root, file)\n', 'Go relative path'),
            ('A.java', 'String rel = root.relativize(file).toString();\n', 'Java relative path'),
        ]
        for path, content, kind in cases:
            with self.subTest(path=path, content=content):
                self.new_session()
                self.assert_denied(
                    self.run_call({'name': 'create', 'args': {'path': path, 'file_text': content}}),
                    [path, kind, f'"{content.strip()}"', 'retry the same edit'], content,
                )

    def test_reason_uses_slash_separated_paths(self):
        # 拒否の理由自体もOSによらず `/` 区切りで示す。
        self.assert_denied(
            self.run_call({'name': 'create', 'args': {
                'path': os.path.join('src', 'tools', 'a.py'), 'file_text': 'x = os.path.relpath(a, b)\n',
            }}),
            ['src/tools/a.py would get Python relative path'], 'nested path',
        )

    def test_normalized_paths_are_allowed(self):
        cases = [
            ('a.py', "return os.path.relpath(path, cwd).replace(os.sep, '/')\n"),
            ('a.py', "return os.path.relpath(path, cwd).replace('\\\\', '/')\n"),
            ('a.py', 'name = path.relative_to(root).as_posix()\n'),
            ('a.py', 'name = posixpath.relpath(a, b)\n'),
            ('a.ts', "const rel = path.relative(root, file).split(path.sep).join('/');\n"),
            ('a.ts', "const rel = path.relative(root, file).replace(/\\\\/g, '/');\n"),
            ('a.ts', "const rel = path.relative(root, file).replaceAll('\\\\', '/');\n"),
            ('a.ts', 'const rel = path.posix.relative(root, file);\n'),
            ('A.cs', "var rel = Path.GetRelativePath(root, file).Replace(Path.DirectorySeparatorChar, '/');\n"),
            ('A.cs', 'var rel = Path.GetRelativePath(root, file).Replace("\\\\", "/");\n'),
            ('a.ps1', "$rel = (Resolve-Path $file -Relative) -replace '\\\\', '/'\n"),
            ('a.go', 'rel := filepath.ToSlash(must(filepath.Rel(root, file)))\n'),
            ('A.java', "String rel = root.relativize(file).toString().replace(File.separatorChar, '/');\n"),
            ('a.py', 'path = os.path.join(root, name)\n'),
            ('a.py', 'full = os.path.realpath(path)\n'),
        ]
        for path, content in cases:
            with self.subTest(path=path, content=content):
                self.assert_allowed(
                    self.run_call({'name': 'create', 'args': {'path': path, 'file_text': content}}), content,
                )

    def test_tool_formats(self):
        line = 'x = os.path.relpath(a, b)\n'
        cases = [
            ('edit', {'path': 'app.py', 'old_str': 'print(show(a, b))\n', 'new_str': line}),
            ('functions.edit', json.dumps({'path': 'app.py', 'old_str': 'print(show(a, b))\n', 'new_str': line})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'app.py', 'old_str': 'print', 'new_str': line}),
            ('apply_patch', f'*** Begin Patch\n*** Update File: app.py\n@@\n-print(show(a, b))\n+{line}*** End Patch\n'),
            ('insert', {'path': 'app.py', 'insert_line': 4, 'new_str': line}),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name):
                self.new_session()
                self.assert_denied(self.run_call({'name': tool_name, 'args': args}), ['app.py'], tool_name)

    def test_existing_lines_are_allowed(self):
        self.assert_allowed(
            self.run_call({'name': 'edit', 'args': {
                'path': 'app.py', 'old_str': 'print(show(a, b))', 'new_str': 'print(show(a, c))',
            }}),
            'Edits around an existing native path must be allowed.',
        )
        self.assert_allowed(
            self.run_call({'name': 'edit', 'args': {
                'path': 'app.py',
                'old_str': '    return os.path.relpath(path, cwd)\n',
                'new_str': '        return os.path.relpath(path, cwd)\n',
            }}),
            'Reindenting an existing native path must be allowed.',
        )

    def test_documents_and_deletions_are_ignored(self):
        for tool_call in (
            {'name': 'create', 'args': {'path': 'docs/README.md', 'file_text': 'Use `os.path.relpath(a, b)`.\n'}},
            {'name': 'create', 'args': {'path': 'NOTES.txt', 'file_text': 'path.relative(a, b)\n'}},
            {'name': 'apply_patch', 'args': '*** Begin Patch\n*** Delete File: app.py\n*** End Patch\n'},
            {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'app.py'}},
            {'name': 'bash', 'args': {'command': 'echo "os.path.relpath(a, b)" > a.py'}},
        ):
            with self.subTest(tool_call=tool_call):
                self.assert_allowed(self.run_call(tool_call), 'Documents, deletions, and non-writing tools must be ignored.')

    def test_denies_only_once_per_file(self):
        call = {'name': 'create', 'args': {'path': 'a.py', 'file_text': 'x = os.path.relpath(a, b)\n'}}
        self.assert_denied(self.run_call(call), ['a.py'], 'first')
        self.assert_allowed(self.run_call(call), 'The same file must not be denied twice.')
        self.assert_denied(
            self.run_call({'name': 'create', 'args': {'path': 'b.py', 'file_text': 'x = os.path.relpath(a, b)\n'}}),
            ['b.py'], 'Another file must still be denied once.',
        )

    def test_patch_files_are_reported_together(self):
        result = self.run_call({'name': 'apply_patch', 'args': (
            '*** Begin Patch\n*** Add File: a.py\n+x = os.path.relpath(a, b)\n'
            '*** Add File: b.ts\n+const x = path.relative(a, b);\n*** End Patch\n'
        )})
        self.assert_denied(result, ['a.py', 'b.ts'], 'patch')

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
                self.assertIn(b'Failed to check native path separators:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = {'sessionId': self.session_id, 'cwd': self.directory, 'toolName': 'edit', 'toolArgs': {}}
        valid_native = [{'name': 'x', 'pattern': 'x'}]
        valid_normalization = ['y']
        for name, content in [
            ('missing file', None),
            ('not json', '{'),
            ('not object', '[]'),
            ('missing excluded patterns', {'nativePathPatterns': valid_native, 'normalizationPatterns': valid_normalization}),
            ('empty native patterns', {'excludedFilePatterns': [], 'nativePathPatterns': [], 'normalizationPatterns': valid_normalization}),
            ('missing pattern', {'excludedFilePatterns': [], 'nativePathPatterns': [{'name': 'x'}], 'normalizationPatterns': valid_normalization}),
            ('empty normalization patterns', {'excludedFilePatterns': [], 'nativePathPatterns': valid_native, 'normalizationPatterns': []}),
            ('invalid excluded regex', {'excludedFilePatterns': ['('], 'nativePathPatterns': valid_native, 'normalizationPatterns': valid_normalization}),
            ('invalid native regex', {'excludedFilePatterns': [], 'nativePathPatterns': [{'name': 'x', 'pattern': '('}], 'normalizationPatterns': valid_normalization}),
            ('invalid normalization regex', {'excludedFilePatterns': [], 'nativePathPatterns': valid_native, 'normalizationPatterns': ['(']}),
        ]:
            with self.subTest(name=name):
                path = os.path.join(self.root, f'definitions-{name.replace(" ", "-")}.json')
                if content is not None:
                    with open(path, 'w', encoding='utf-8') as file:
                        file.write(content if isinstance(content, str) else json.dumps(content))
                result = self.run_process(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check native path separators:', result.stderr)


if __name__ == '__main__':
    run_tests()
