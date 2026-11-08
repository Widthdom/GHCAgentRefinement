import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-exclusion-escapes.py')
_definitions_path = os.path.join(_script_directory, 'exclusion-definitions.json')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_get_hook_entry = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-entry.py'))['get_hook_entry']

_TRACKED_FILES = {
    'src/calc.py': 'def add(a, b):\n    return a + b\n',
    'tests/test_calc.py': 'def test_add():\n    assert False\n',
    'tests/test_slow.py': 'def test_slow():\n    pass\n',
    'web/src/app.ts': 'export const x = 1;\n',
    'web/src/app.test.ts': 'test("x", () => {});\n',
    'web/package.json': '{\n  "name": "web",\n  "jest": {\n    "testPathIgnorePatterns": ["/node_modules/"]\n  }\n}\n',
    'web/tsconfig.json': '{\n  "compilerOptions": {},\n  "exclude": ["node_modules"]\n}\n',
    'Calc/Calc.csproj': '<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n  </ItemGroup>\n</Project>\n',
    'Calc/Broken.cs': 'class Broken {}\n',
    'pyproject.toml': '[tool.pytest.ini_options]\naddopts = "-q"\n\n[tool.ruff]\nextend-exclude = ["build"]\n',
    'setup.cfg': '[coverage:run]\nomit =\n    build/*\n\n[metadata]\nname = calc\n',
    '.eslintignore': 'dist/\n',
}


@unittest.skipUnless(shutil.which('git'), 'git is required to list tracked files.')
class BlockExclusionEscapesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-exclusion-escapes-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__directory = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__directory)
        for path, content in _TRACKED_FILES.items():
            self.__write_file(path, content)
        subprocess.run(['git', 'init', '-q'], cwd=self.__directory, check=True)
        subprocess.run(['git', 'add', '-A'], cwd=self.__directory, check=True)
        self.__new_session()

    def __write_file(self, path, content):
        full_path = os.path.join(self.__directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __new_session(self):
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__root, 'ghc-agent-refinement', 'hook-states', 'block-exclusion-escapes', f'{self.__session_id}.sqlite3',
        )

    def __run_hook(self, hook_input, *arguments):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __run_call(self, tool_name, tool_args, cwd=None):
        return self.__run_hook({
            'sessionId': self.__session_id, 'cwd': cwd or self.__directory, 'toolName': tool_name, 'toolArgs': tool_args,
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

    def test_definitions_are_valid_json(self):
        with open(_definitions_path, encoding='utf-8') as file:
            definitions = json.load(file)
        self.assertTrue(definitions['ignoreFiles'])
        self.assertTrue(definitions['configFiles'])

    def test_configuration(self):
        entry = _get_hook_entry(_hook_path)
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertRegex(tool_name, f'^(?:{entry["matcher"]})$')
        self.assertNotRegex('bash', f'^(?:{entry["matcher"]})$')

    def test_excluding_tracked_files_is_denied(self):
        cases = [
            ('pytest --ignore', 'edit', {
                'path': 'pyproject.toml', 'old_str': 'addopts = "-q"',
                'new_str': 'addopts = "-q --ignore=tests/test_calc.py"',
            }, ['pyproject.toml adds the exclusion "tests/test_calc.py"', 'tracked file tests/test_calc.py']),
            ('pytest --deselect list', 'edit', {
                'path': 'pyproject.toml', 'old_str': 'addopts = "-q"',
                'new_str': 'addopts = ["-q", "--deselect", "tests/test_calc.py::test_add"]',
            }, ['"tests/test_calc.py"']),
            ('ruff extend-exclude', 'edit', {
                'path': 'pyproject.toml', 'old_str': 'extend-exclude = ["build"]',
                'new_str': 'extend-exclude = ["build", "src/calc.py"]',
            }, ['"src/calc.py"']),
            ('coverage omit continuation', 'edit', {
                'path': 'setup.cfg', 'old_str': '    build/*\n', 'new_str': '    build/*\n    src/*\n',
            }, ['setup.cfg adds the exclusion "src"', 'src/calc.py']),
            ('conftest collect_ignore', 'create', {
                'path': 'tests/conftest.py', 'file_text': 'collect_ignore = ["test_calc.py"]\n',
            }, ['tests/conftest.py adds the exclusion "test_calc.py"', 'tests/test_calc.py']),
            ('jest testPathIgnorePatterns regex', 'edit', {
                'path': 'web/package.json', 'old_str': '["/node_modules/"]',
                'new_str': '["/node_modules/", "<rootDir>/src/app\\\\.test\\\\.ts"]',
            }, ['web/package.json adds the exclusion "src/app.test.ts"', 'web/src/app.test.ts']),
            ('tsconfig exclude glob', 'str_replace_editor', {
                'command': 'str_replace', 'path': 'web/tsconfig.json', 'old_str': '["node_modules"]',
                'new_str': '["node_modules", "src/**/*.test.ts"]',
            }, ['"src/**/*.test.ts"']),
            ('csproj Compile Remove', 'insert', {
                'path': 'Calc/Calc.csproj', 'insert_line': 2, 'new_str': '    <Compile Remove="Broken.cs" />\n',
            }, ['Calc/Calc.csproj adds the exclusion "Broken.cs"', 'Calc/Broken.cs']),
            ('eslintignore', 'apply_patch',
             '*** Begin Patch\n*** Update File: .eslintignore\n@@\n dist/\n+src/\n*** End Patch\n',
             ['.eslintignore adds the exclusion "src"']),
            ('new eslintignore', 'apply_patch',
             '*** Begin Patch\n*** Add File: web/.eslintignore\n+src/app.ts\n*** End Patch\n',
             ['web/.eslintignore adds the exclusion "src/app.ts"']),
        ]
        for name, tool_name, args, expected in cases:
            with self.subTest(name=name):
                self.__new_session()
                self.__assert_denied(self.__run_call(tool_name, args), [*expected, 'only once per file'], name)

    def test_harmless_edits_are_allowed(self):
        cases = [
            ('untracked build output', 'edit', {
                'path': 'pyproject.toml', 'old_str': 'extend-exclude = ["build"]',
                'new_str': 'extend-exclude = ["build", "dist", ".venv"]',
            }),
            ('existing entry reformatted', 'edit', {
                'path': 'pyproject.toml', 'old_str': 'extend-exclude = ["build"]', 'new_str': "extend-exclude = ['build']",
            }),
            ('removing an exclusion', 'edit', {
                'path': 'setup.cfg', 'old_str': 'omit =\n    build/*\n', 'new_str': 'omit =\n',
            }),
            ('negated entry', 'apply_patch',
             '*** Begin Patch\n*** Update File: .eslintignore\n@@\n dist/\n+!src/app.ts\n*** End Patch\n'),
            ('comment in ignore file', 'apply_patch',
             '*** Begin Patch\n*** Update File: .eslintignore\n@@\n dist/\n+# src is linted\n*** End Patch\n'),
            ('other keys', 'edit', {
                'path': 'setup.cfg', 'old_str': 'name = calc', 'new_str': 'name = src',
            }),
            ('keyword in prose', 'edit', {
                'path': 'pyproject.toml', 'old_str': 'addopts = "-q"', 'new_str': '# exclude src later\naddopts = "-q"',
            }),
            ('not a config file', 'edit', {
                'path': 'src/calc.py', 'old_str': 'return a + b', 'new_str': 'exclude = ["tests"]\n    return a + b',
            }),
            ('gitignore is out of scope', 'create', {'path': '.gitignore', 'file_text': 'src/\n'}),
        ]
        for name, tool_name, args in cases:
            with self.subTest(name=name):
                self.__assert_allowed(self.__run_call(tool_name, args), name)
        self.assertFalse(os.path.exists(self.__state_path), 'Allowed edits must not create the state file.')

    def test_same_file_is_denied_only_once(self):
        args = {'path': 'pyproject.toml', 'old_str': 'addopts = "-q"', 'new_str': 'addopts = "-q --ignore=tests"'}
        self.__assert_denied(self.__run_call('edit', args), ['"tests"'], 'First edit must be denied.')
        self.__assert_allowed(self.__run_call('edit', args), 'Retry must be allowed.')

    def test_outside_git_repository_is_allowed(self):
        outside = os.path.join(self.__root, 'outside')
        os.makedirs(outside)
        with open(os.path.join(outside, 'calc.py'), 'w', encoding='utf-8', newline='\n') as file:
            file.write('x = 1\n')
        self.__assert_allowed(
            self.__run_call('create', {'path': 'pytest.ini', 'file_text': '[pytest]\naddopts = --ignore=calc.py\n'}, cwd=outside),
            'Files outside a git repository cannot be judged.',
        )

    def test_undetermined_edits_and_other_tools_are_ignored(self):
        cases = [
            ('old_str not found', 'edit', {'path': 'pyproject.toml', 'old_str': 'missing', 'new_str': 'addopts = "--ignore=src"'}),
            ('view', 'str_replace_editor', {'command': 'view', 'path': 'pyproject.toml'}),
            ('delete', 'apply_patch', '*** Begin Patch\n*** Delete File: .eslintignore\n*** End Patch\n'),
            ('shell', 'bash', {'command': 'echo src/ >> .eslintignore'}),
        ]
        for name, tool_name, args in cases:
            with self.subTest(name=name):
                self.__assert_allowed(self.__run_call(tool_name, args), name)

    def test_invalid_input_exits_with_error(self):
        cases = [
            ('empty input', b''),
            ('invalid JSON', b'{'),
            ('non-object', b'[]'),
            ('missing sessionId', {'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}}),
            ('invalid sessionId', {'sessionId': 'abc', 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}}),
            ('missing cwd', {'sessionId': self.__session_id, 'toolName': 'edit', 'toolArgs': {}}),
            ('relative cwd', {'sessionId': self.__session_id, 'cwd': 'workspace', 'toolName': 'edit', 'toolArgs': {}}),
            ('missing toolName', {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolArgs': {}}),
            ('missing toolArgs', {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit'}),
        ]
        for name, hook_input in cases:
            with self.subTest(name=name):
                result = self.__run_hook(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check exclusion escapes', result.stderr, name)

    def test_invalid_definitions_exit_with_error(self):
        config = {'name': 'x', 'filePattern': 'x', 'valuePatterns': [], 'keywordPatterns': ['y']}
        cases = [
            ('not an object', []),
            ('empty configFiles', {'ignoreFiles': [], 'configFiles': []}),
            ('no patterns', {'ignoreFiles': [], 'configFiles': [{**config, 'keywordPatterns': []}]}),
            ('value without group', {'ignoreFiles': [], 'configFiles': [{**config, 'valuePatterns': ['a']}]}),
            ('invalid regex', {'ignoreFiles': ['('], 'configFiles': [config]}),
            ('missing name', {'ignoreFiles': [], 'configFiles': [{**config, 'name': ''}]}),
        ]
        for name, definitions in cases:
            with self.subTest(name=name):
                path = os.path.join(self.__root, 'definitions.json')
                with open(path, 'w', encoding='utf-8', newline='\n') as file:
                    json.dump(definitions, file)
                result = self.__run_hook(
                    {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}},
                    '--definitions-path', path,
                )
                self.assertEqual(result.returncode, 2, name)


if __name__ == '__main__':
    _run_tests()
