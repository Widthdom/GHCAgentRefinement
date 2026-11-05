import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-native-newlines.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_ORIGINAL = (
    'def main():\n'
    "    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8')\n"
    "    with open(path, 'w', encoding='utf-8') as file:\n"
    '        file.write(text)\n'
)


class BlockNativeNewlinesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-native-newlines-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__directory = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__directory)
        self.__write_file('app.py', _ORIGINAL)
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

    def __run_call(self, tool_name, tool_args):
        return self.__run_process({
            'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': tool_name, 'toolArgs': tool_args,
        })

    def __create(self, path, content):
        return self.__run_call('create', {'path': path, 'file_text': content})

    def __assert_allowed(self, result, message=None):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_denied(self, result, *expected_parts):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\r\n', result.stdout + result.stderr)
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny')
        reason = output['permissionDecisionReason']
        self.assertTrue(reason.startswith('Edit denied: '))
        self.assertEqual(result.stderr.decode('utf-8').strip(), reason)
        for part in expected_parts:
            self.assertIn(part, reason)
        return reason

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, '-repo-preToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/-repo-events/preToolUse/block-native-newlines/block-native-newlines.py'
        entries = configuration['hooks']['preToolUse']
        matching = [entry for entry in entries if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}']
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertRegex(tool_name, f'^(?:{matching[0]["matcher"]})$')
        self.assertNotRegex('bash', f'^(?:{matching[0]["matcher"]})$')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_unpinned_calls_are_denied(self):
        cases = [
            ("cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8')\n", 'stdout/stderr reconfigure'),
            ("sys.stderr.reconfigure(encoding='utf-8', errors='replace')\n", 'stdout/stderr reconfigure'),
            ("with open(path, 'w', encoding='utf-8') as file:\n", 'text-mode open for writing'),
            ("with open(path, mode='a') as file:\n", 'text-mode open for writing'),
            ("handle = open(path, 'r+', encoding='utf-8')\n", 'text-mode open for writing'),
            ("with io.open(path, 'wt') as file:\n", 'text-mode open for writing'),
            ("with Path(path).open('x', encoding='utf-8') as file:\n", 'text-mode open for writing'),
            ("stream = os.fdopen(descriptor, 'w')\n", 'text-mode open for writing'),
            ("with gzip.open(path, 'wt', encoding='utf-8') as file:\n", 'text-mode open for writing'),
            ("with open(\n    path,\n    'w',\n    encoding='utf-8',\n) as file:\n", 'text-mode open for writing'),
            ("Path(path).write_text(text, encoding='utf-8')\n", 'Path.write_text'),
            ("stream = io.TextIOWrapper(buffer, encoding='utf-8')\n", 'TextIOWrapper'),
            ("with tempfile.NamedTemporaryFile(mode='w', delete=False) as file:\n", 'text-mode temporary file'),
        ]
        for content, kind in cases:
            with self.subTest(content=content):
                self.__new_session()
                self.__assert_denied(self.__create('new.py', content), 'new.py would get', kind)

    def test_pinned_and_binary_calls_are_allowed(self):
        cases = [
            "cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\\n')\n",
            "with open(path, 'w', encoding='utf-8', newline='\\n') as file:\n",
            "with open(path, 'w', encoding='utf-8', newline='') as file:\n",
            "with open(\n    path,\n    'w',\n    encoding='utf-8',\n    newline='\\n',\n) as file:\n",
            "with open(path, 'wb') as file:\n",
            "with open(path, 'rb') as file:\n",
            "with open(path, encoding='utf-8') as file:\n",
            "with open(path, 'r', encoding='utf-8') as file:\n",
            "with open(path, mode) as file:\n",
            "Path(path).write_text(text, encoding='utf-8', newline='\\n')\n",
            "Path(path).write_bytes(data)\n",
            "stream = io.TextIOWrapper(buffer, encoding='utf-8', newline='\\n')\n",
            "with tempfile.NamedTemporaryFile(delete=False) as file:\n",
            "with tempfile.NamedTemporaryFile(\n    mode='w', encoding='utf-8', newline='',\n) as file:\n",
            "webbrowser.open(url)\n",
            "sys.stdout.write(text)\n",
        ]
        for content in cases:
            with self.subTest(content=content):
                self.__assert_allowed(self.__create('new.py', content))

    def test_strings_and_comments_are_ignored(self):
        cases = [
            "command = \"python -c \\\"open('x.json', 'w').write('{}')\\\"\"\n",
            "code = '''\nwith open(path, 'w') as file:\n    pass\n'''\n",
            "# with open(path, 'w') as file:\n",
            "value = 1  # sys.stdout.reconfigure(encoding='utf-8')\n",
        ]
        for content in cases:
            with self.subTest(content=content):
                self.__assert_allowed(self.__create('new.py', content))

    def test_tool_formats(self):
        line = "Path(path).write_text(text)"
        calls = [
            ('edit', {'path': 'app.py', 'old_str': '        file.write(text)', 'new_str': f'        {line}'}),
            ('functions.edit', json.dumps({'path': 'app.py', 'old_str': 'x', 'new_str': line})),
            ('insert', {'path': 'app.py', 'insert_line': 1, 'new_str': line}),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'app.py', 'old_str': 'x', 'new_str': line}),
            ('apply_patch', f'*** Begin Patch\n*** Update File: app.py\n@@\n-        file.write(text)\n+        {line}\n*** End Patch'),
            ('apply_patch', {'input': f'*** Begin Patch\n*** Add File: pkg/new.py\n+{line}\n*** End Patch'}),
        ]
        for tool_name, tool_args in calls:
            with self.subTest(tool_name=tool_name):
                self.__new_session()
                self.__assert_denied(self.__run_call(tool_name, tool_args), 'Path.write_text')

    def test_existing_lines_are_allowed(self):
        # 既存の行と同じ行は、インデントが変わっても元からあるものとして扱う。
        self.__assert_allowed(self.__run_call('edit', {
            'path': 'app.py',
            'old_str': "    with open(path, 'w', encoding='utf-8') as file:\n        file.write(text)",
            'new_str': "    if text:\n        with open(path, 'w', encoding='utf-8') as file:\n            file.write(text)",
        }))
        self.__assert_allowed(self.__run_call('edit', {
            'path': 'other.py', 'old_str': "open(path, 'a')", 'new_str': "    open(path, 'a')",
        }))

    def test_non_python_files_and_other_tools_are_ignored(self):
        content = "with open(path, 'w') as file:\n"
        for path in ('README.md', 'script.js', 'data.json', 'tool.ps1', 'notes.txt'):
            with self.subTest(path=path):
                self.__assert_allowed(self.__create(path, content))
        self.__assert_allowed(self.__run_call('bash', {'command': f"python -c \"{content}\""}))
        self.__assert_allowed(self.__run_call('str_replace_editor', {'command': 'view', 'path': 'app.py'}))
        self.__assert_allowed(self.__run_call('apply_patch', '*** Begin Patch\n*** Delete File: app.py\n*** End Patch'))
        self.__assert_denied(self.__create('stub.pyi', content), 'stub.pyi')

    def test_reason_lists_kinds_and_snippets(self):
        content = (
            "cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8')\n"
            "with open(a, 'w') as file:\n"
            "with open(b, 'w') as file:\n"
            "with open(c, 'w') as file:\n"
            "with open(d, 'w') as file:\n"
            f"x = open('{'x' * 100}', 'w')\n"
        )
        reason = self.__assert_denied(
            self.__create('pkg/sub/new.py', content),
            'pkg/sub/new.py would get stdout/stderr reconfigure, text-mode open for writing without newline=',
            "\"with open(a, 'w') as file:\"",
            "newline='\\n'",
        )
        self.assertNotIn('with open(c', reason)

    def test_denies_only_once_per_file(self):
        content = "with open(path, 'w') as file:\n"
        self.__assert_denied(self.__create('new.py', content), 'new.py')
        self.__assert_allowed(self.__create('new.py', content))
        self.__assert_denied(self.__create('other.py', content), 'other.py')

    def test_patch_files_are_reported_together(self):
        patch = (
            '*** Begin Patch\n'
            '*** Add File: a.py\n'
            "+with open(path, 'w') as file:\n"
            '*** Add File: b.py\n'
            "+Path(path).write_text(text)\n"
            '*** End Patch'
        )
        self.__assert_denied(self.__run_call('apply_patch', patch), 'a.py would get', 'b.py would get')

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
                self.assertIn(b'Failed to check native newlines:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}}
        valid = {
            'includedFilePatterns': ['x'],
            'translatingCalls': [{'name': 'x', 'pattern': 'x', 'requiresTextWriteMode': False}],
            'textWriteModePattern': 'x',
            'pinnedNewlinePattern': 'x',
        }
        for name, content in [
            ('missing file', None),
            ('not json', '{'),
            ('not object', '[]'),
            ('empty included patterns', {**valid, 'includedFilePatterns': []}),
            ('empty calls', {**valid, 'translatingCalls': []}),
            ('missing pattern', {**valid, 'translatingCalls': [{'name': 'x', 'requiresTextWriteMode': False}]}),
            ('missing mode flag', {**valid, 'translatingCalls': [{'name': 'x', 'pattern': 'x'}]}),
            ('missing mode pattern', {key: value for key, value in valid.items() if key != 'textWriteModePattern'}),
            ('empty pinned pattern', {**valid, 'pinnedNewlinePattern': ' '}),
            ('invalid included regex', {**valid, 'includedFilePatterns': ['(']}),
            ('invalid call regex', {**valid, 'translatingCalls': [{'name': 'x', 'pattern': '(', 'requiresTextWriteMode': True}]}),
            ('invalid pinned regex', {**valid, 'pinnedNewlinePattern': '('}),
        ]:
            with self.subTest(name=name):
                path = os.path.join(self.__root, f'definitions-{name.replace(" ", "-")}.json')
                if content is not None:
                    with open(path, 'w', encoding='utf-8', newline='\n') as file:
                        file.write(content if isinstance(content, str) else json.dumps(content))
                result = self.__run_process(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check native newlines:', result.stderr)


if __name__ == '__main__':
    run_tests()
