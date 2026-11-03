import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-rename-leftovers.py')

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


def _run_hook(input_bytes):
    return subprocess.run([sys.executable, _hook_path], input=input_bytes, capture_output=True)


class DetectRenameLeftoversTests(unittest.TestCase):
    def setUp(self):
        if shutil.which('git') is None:
            self.skipTest('git is not installed.')
        self.__directory = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.__directory, ignore_errors=True)
        subprocess.run(['git', 'init', '-q', self.__directory], check=True)

    def __write_file(self, path, content, directory=None):
        full_path = os.path.join(directory or self.__directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __run_call(self, tool_name, tool_args, cwd=None):
        hook_input = {
            'sessionId': '00000000-0000-4000-8000-000000000000', 'cwd': cwd or self.__directory,
            'toolName': tool_name, 'toolArgs': tool_args, 'toolResult': {'resultType': 'success'},
        }
        return _run_hook(json.dumps(hook_input).encode('utf-8'))

    def __edit(self, path, old, new, cwd=None):
        return self.__run_call('edit', {'path': path, 'old_str': old, 'new_str': new}, cwd)

    def __assert_silent(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_context(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        context = json.loads(result.stdout.decode('utf-8'))['additionalContext']
        for part in expected_parts:
            self.assertIn(part, context, message)
        return context

    def test_leftover_references_are_reported(self):
        cases = [
            ('src/Calc.cs', 'public class Calculator\n{', 'public class Calc\n{', 'Calculator', 'Calc',
             'var c = new Calculator();\n'),
            ('src/Calc.cs', '    public int AddNumbers(int a, int b)', '    public int Add(int a, int b)', 'AddNumbers', 'Add',
             'calc.AddNumbers(1, 2);\n'),
            ('src/Calc.cs', '    public string DisplayName { get; set; }', '    public string Title { get; set; }',
             'DisplayName', 'Title', '<TextBlock Text="{Binding DisplayName}" />\n'),
            ('app/parser.py', 'def parse_line(text):', 'def parse_row(text):', 'parse_line', 'parse_row',
             'from app.parser import parse_line\n'),
            ('app/config.py', 'MAX_RETRIES = 3', 'RETRY_LIMIT = 3', 'MAX_RETRIES', 'RETRY_LIMIT', 'Set `MAX_RETRIES`.\n'),
            ('src/util.ts', 'export function formatDate(d) {', 'export function formatDay(d) {', 'formatDate', 'formatDay',
             "import { formatDate } from './util';\n"),
            ('src/util.ts', 'export const apiBase = "/api";', 'export const apiRoot = "/api";', 'apiBase', 'apiRoot',
             'fetch(apiBase);\n'),
            ('main.go', 'func LoadConfig() {', 'func ReadConfig() {', 'LoadConfig', 'ReadConfig', 'LoadConfig()\n'),
        ]
        for path, old, new, old_name, new_name, reference in cases:
            with self.subTest(old_name=old_name):
                self.__write_file(path, f'{new}\n')
                self.__write_file('docs/usage.md', reference)
                self.__assert_context(
                    self.__edit(path, old, new),
                    [f'`{old_name}` to `{new_name}`', f'docs/usage.md:1 (`{old_name}`)'], old_name,
                )

    def test_leftover_in_the_edited_file_is_reported(self):
        self.__write_file('app/parser.py', 'def parse_row(text):\n    return text\n\nresult = parse_line("a")\n')
        self.__assert_context(self.__edit('app/parser.py', 'def parse_line(text):', 'def parse_row(text):'), ['app/parser.py:4'], 'same file')

    def test_apply_patch_is_checked(self):
        self.__write_file('app/parser.py', 'def parse_row(text):\n    return text\n')
        self.__write_file('app/main.py', 'parse_line("a")\n')
        patch = (
            '*** Begin Patch\n*** Update File: app/parser.py\n@@\n'
            '-def parse_line(text):\n+def parse_row(text):\n     return text\n*** End Patch\n'
        )
        self.__assert_context(self.__run_call('apply_patch', patch), ['`parse_line` to `parse_row`', 'app/main.py:1'], 'patch')

    def test_locations_are_limited(self):
        self.__write_file('app/parser.py', 'def parse_row(text):\n')
        self.__write_file('app/main.py', ''.join('parse_line()\n' for _ in range(12)))
        context = self.__assert_context(self.__edit('app/parser.py', 'def parse_line(text):', 'def parse_row(text):'), ['and 2 more'], 'limit')
        self.assertNotIn('app/main.py:11', context)

    def test_complete_renames_are_silent(self):
        self.__write_file('app/parser.py', 'def parse_row(text):\n')
        self.__write_file('app/main.py', 'parse_row("a")\nparse_lines("b")\n')
        self.__assert_silent(self.__edit('app/parser.py', 'def parse_line(text):', 'def parse_row(text):'), 'no leftovers')

    def test_non_renames_are_silent(self):
        self.__write_file('app/main.py', 'value = 1\ncount = 2\nrun()\nhelper()\n')
        cases = [
            # 定義でないローカル変数の名前の変更。
            ('    value = 1', '    total = 1'),
            # 定義の本体だけの変更。
            ('def run():\n    return 1', 'def run():\n    return 2'),
            # 定義の追加と削除。
            ('def helper():\n    pass', ''),
            ('', 'def helper2():\n    pass'),
            # 古い名前が置換後にも残る（オーバーロードの追加など）。
            ('def run():', 'def run():\n    pass\n\ndef run_all():'),
            # 短すぎる名前。
            ('def go():', 'def do():'),
        ]
        for old, new in cases:
            with self.subTest(old=old, new=new):
                self.__assert_silent(self.__edit('app/x.py', old, new), old)

    def test_untracked_ignored_files_are_excluded(self):
        self.__write_file('.gitignore', 'build/\n')
        self.__write_file('build/out.py', 'parse_line()\n')
        self.__write_file('app/parser.py', 'def parse_row(text):\n')
        self.__assert_silent(self.__edit('app/parser.py', 'def parse_line(text):', 'def parse_row(text):'), 'ignored')

    def test_outside_repository_is_silent(self):
        directory = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        self.__write_file('main.py', 'parse_line()\n', directory)
        result = self.__edit('parser.py', 'def parse_line(text):', 'def parse_row(text):', directory)
        if subprocess.run(['git', '-C', directory, 'rev-parse'], capture_output=True).returncode == 0:
            self.skipTest('the temporary directory is inside a git repository.')
        self.__assert_silent(result, 'outside repository')

    def test_other_tools_are_skipped(self):
        self.__write_file('app/main.py', 'parse_line()\n')
        calls = [
            ('create', {'path': 'app/parser.py', 'file_text': 'def parse_row(text):\n'}),
            ('insert', {'path': 'app/parser.py', 'insert_line': 1, 'new_str': 'def parse_row(text):\n'}),
            ('str_replace_editor', {'command': 'view', 'path': 'app/parser.py'}),
            ('bash', {'command': 'sed -i s/parse_line/parse_row/ app/parser.py'}),
        ]
        for tool_name, tool_args in calls:
            with self.subTest(tool_name=tool_name):
                self.__assert_silent(self.__run_call(tool_name, tool_args), tool_name)

    def test_str_replace_editor_is_checked(self):
        self.__write_file('app/parser.py', 'def parse_row(text):\n')
        self.__write_file('app/main.py', 'parse_line()\n')
        result = self.__run_call('functions.str_replace_editor', {
            'command': 'str_replace', 'path': 'app/parser.py', 'old_str': 'def parse_line(text):', 'new_str': 'def parse_row(text):',
        })
        self.__assert_context(result, ['app/main.py:1'], 'str_replace_editor')

    def test_invalid_input_fails(self):
        inputs = [
            b'',
            b'[]',
            json.dumps({'cwd': 'relative', 'toolName': 'edit', 'toolArgs': {}}).encode(),
            json.dumps({'cwd': self.__directory, 'toolArgs': {}}).encode(),
            json.dumps({'cwd': self.__directory, 'toolName': 'edit'}).encode(),
        ]
        for input_bytes in inputs:
            with self.subTest(input_bytes=input_bytes):
                result = _run_hook(input_bytes)
                self.assertEqual(result.returncode, 2, input_bytes)
                self.assertIn(b'Failed to detect rename leftovers', result.stderr)


if __name__ == '__main__':
    run_tests()
