import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-error-swallowing.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_ORIGINAL = (
    'def load(path):\n'
    '    try:\n'
    '        return read(path)\n'
    '    except OSError:\n'
    '        raise\n'
)


class BlockErrorSwallowingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-error-swallowing-test-')
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

    def __run_call(self, tool_call):
        return self.__run_process({'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': tool_call['name'], 'toolArgs': tool_call['args']})

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
        relative_path = '.github/hooks/helpers/events/preToolUse/block-error-swallowing/block-error-swallowing.py'
        entries = [
            entry for entry in configuration['hooks']['preToolUse'] if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        self.assertNotRegex('bash', f'^(?:{entries[0]["matcher"]})$')

    def test_swallowing_code_is_denied(self):
        cases = [
            ('a.py', 'try:\n    run()\nexcept:\n    pass\n', 'empty Python exception handler'),
            ('a.py', 'try:\n    run()\nexcept Exception as ex:  # ignore\n    ...\n', 'empty Python exception handler'),
            ('a.py', 'try: run()\nexcept ValueError: pass\n', 'empty Python exception handler'),
            ('a.py', 'with contextlib.suppress(Exception):\n    run()\n', 'suppressed Python exception'),
            ('a.ts', 'try { run(); } catch (e) {}\n', 'empty catch block'),
            ('A.cs', 'try\n{\n    Run();\n}\ncatch\n{\n    // ignore\n}\n', 'empty catch block'),
            ('A.java', 'try { run(); } catch (IOException e) { /* nothing */ }\n', 'empty catch block'),
            ('a.js', 'fetch(url).catch(() => {});\n', 'empty promise catch'),
            ('a.js', 'fetch(url).catch((e) => null);\n', 'empty promise catch'),
            ('a.go', 'v, err := run()\n_ = err\n', 'discarded Go error'),
            ('a.go', 'if err != nil {\n}\n', 'discarded Go error'),
            ('a.sh', 'make build || true\n', 'ignored command failure'),
            ('a.sh', 'rm -rf out || :\n', 'ignored command failure'),
            ('a.sh', 'set +e\n', 'ignored command failure'),
            ('a.yml', '  run: npm test || true\n', 'ignored command failure'),
            ('a.ps1', 'Remove-Item x -ErrorAction SilentlyContinue\n', 'ignored PowerShell error'),
            ('a.ps1', "$ErrorActionPreference = 'SilentlyContinue'\n", 'ignored PowerShell error'),
            ('a.py', 'value = parse(x)  # type: ignore\n', 'suppressed diagnostic'),
            ('a.py', 'import os  # noqa: F401\n', 'suppressed diagnostic'),
            ('a.ts', '// @ts-ignore\nconst x: number = y;\n', 'suppressed diagnostic'),
            ('a.ts', '/* eslint-disable */\n', 'suppressed diagnostic'),
            ('A.cs', '#pragma warning disable CS0168\n', 'suppressed diagnostic'),
            ('A.java', '@SuppressWarnings("unchecked")\n', 'suppressed diagnostic'),
            ('a.go', 'x := y //nolint\n', 'suppressed diagnostic'),
            ('a.rs', '#[allow(unused)]\n', 'suppressed diagnostic'),
        ]
        for path, content, kind in cases:
            with self.subTest(path=path, content=content):
                self.__new_session()
                self.__assert_denied(
                    self.__run_call({'name': 'create', 'args': {'path': path, 'file_text': content}}),
                    [path, kind, 'retry the same edit'], content,
                )

    def test_handled_errors_are_allowed(self):
        cases = [
            ('a.py', 'try:\n    run()\nexcept OSError:\n    log(ex)\n    raise\n'),
            ('a.py', 'except ValueError:\n    return None\n'),
            ('a.py', 'password = "x"\n'),
            ('a.ts', 'try { run(); } catch (e) { console.error(e); throw e; }\n'),
            ('a.js', 'fetch(url).catch((e) => report(e));\n'),
            ('a.go', 'if err != nil {\n    return err\n}\n'),
            ('a.sh', 'if [ "$a" ] || [ "$b" ]; then\n  echo ok\nfi\n'),
            ('a.sh', 'command -v jq 2>/dev/null\n'),
            ('a.sh', 'set -e\n'),
            ('a.js', 'const ok = a || truthy;\n'),
            ('a.ps1', 'Get-Item x -ErrorAction Stop\n'),
            ('a.py', 'kind = "type"  # ignored later\n'),
        ]
        for path, content in cases:
            with self.subTest(path=path, content=content):
                self.__assert_allowed(
                    self.__run_call({'name': 'create', 'args': {'path': path, 'file_text': content}}), content,
                )

    def test_tool_formats(self):
        cases = [
            ('edit', {'path': 'app.py', 'old_str': '        raise\n', 'new_str': '        pass\n'}),
            ('functions.edit', json.dumps({'path': 'app.py', 'old_str': '        raise\n', 'new_str': '        pass\n'})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'app.py', 'old_str': 'raise', 'new_str': 'pass'}),
            ('apply_patch', '*** Begin Patch\n*** Update File: app.py\n@@\n-    except OSError:\n-        raise\n'
                            '+    except OSError:\n+        pass\n*** End Patch\n'),
            ('insert', {'path': 'app.py', 'insert_line': 5, 'new_str': 'except: pass\n'}),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name):
                self.__new_session()
                self.__assert_denied(self.__run_call({'name': tool_name, 'args': args}), ['app.py'], tool_name)

    def test_edits_around_existing_swallowing_are_allowed(self):
        self.__write_file('legacy.sh', 'cleanup || true\necho done\n')
        self.__assert_allowed(
            self.__run_call({'name': 'edit', 'args': {
                'path': 'legacy.sh', 'old_str': 'echo done', 'new_str': 'echo finished',
            }}),
            'Swallowing that the file already has must be allowed.',
        )
        self.__assert_allowed(
            self.__run_call({'name': 'edit', 'args': {
                'path': 'legacy.sh', 'old_str': 'cleanup || true\n', 'new_str': '    cleanup || true\n',
            }}),
            'Reindenting existing swallowing must be allowed.',
        )

    def test_documents_and_deletions_are_ignored(self):
        for tool_call in (
            {'name': 'create', 'args': {'path': 'docs/README.md', 'file_text': 'Run `make || true`.\n'}},
            {'name': 'create', 'args': {'path': 'NOTES.txt', 'file_text': 'except: pass\n'}},
            {'name': 'apply_patch', 'args': '*** Begin Patch\n*** Delete File: app.py\n*** End Patch\n'},
            {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'app.py'}},
            {'name': 'bash', 'args': {'command': 'echo "|| true" > a.sh'}},
        ):
            with self.subTest(tool_call=tool_call):
                self.__assert_allowed(self.__run_call(tool_call), 'Documents, deletions, and non-writing tools must be ignored.')

    def test_denies_only_once_per_file(self):
        call = {'name': 'create', 'args': {'path': 'a.sh', 'file_text': 'make || true\n'}}
        self.__assert_denied(self.__run_call(call), ['a.sh'], 'first')
        self.__assert_allowed(self.__run_call(call), 'The same file must not be denied twice.')
        self.__assert_denied(
            self.__run_call({'name': 'create', 'args': {'path': 'b.sh', 'file_text': 'make || true\n'}}),
            ['b.sh'], 'Another file must still be denied once.',
        )

    def test_patch_files_are_reported_together(self):
        result = self.__run_call({'name': 'apply_patch', 'args': (
            '*** Begin Patch\n*** Add File: a.sh\n+make || true\n'
            '*** Add File: b.py\n+x = y  # type: ignore\n*** End Patch\n'
        )})
        self.__assert_denied(result, ['a.sh', 'b.py'], 'patch')

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
                self.assertIn(b'Failed to check error swallowing:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}}
        valid_swallowing = [{'name': 'x', 'pattern': 'x'}]
        for name, content in [
            ('missing file', None),
            ('not json', '{'),
            ('not object', '[]'),
            ('missing excluded patterns', {'swallowingPatterns': valid_swallowing}),
            ('empty swallowing patterns', {'excludedFilePatterns': [], 'swallowingPatterns': []}),
            ('missing pattern', {'excludedFilePatterns': [], 'swallowingPatterns': [{'name': 'x'}]}),
            ('invalid excluded regex', {'excludedFilePatterns': ['('], 'swallowingPatterns': valid_swallowing}),
            ('invalid swallowing regex', {'excludedFilePatterns': [], 'swallowingPatterns': [{'name': 'x', 'pattern': '('}]}),
        ]:
            with self.subTest(name=name):
                path = os.path.join(self.__root, f'definitions-{name.replace(" ", "-")}.json')
                if content is not None:
                    with open(path, 'w', encoding='utf-8', newline='\n') as file:
                        file.write(content if isinstance(content, str) else json.dumps(content))
                result = self.__run_process(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check error swallowing:', result.stderr)


if __name__ == '__main__':
    run_tests()
