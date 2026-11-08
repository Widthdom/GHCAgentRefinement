import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-timeout-inflation.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_get_hook_entry = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-entry.py'))['get_hook_entry']


def _edit(path, old, new):
    return {'name': 'edit', 'args': {'path': path, 'old_str': old, 'new_str': new}}


class BlockTimeoutInflationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-timeout-inflation-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__directory = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__directory)
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

    def __assert_allowed(self, result, message=None):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_denied(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny')
        reason = output['permissionDecisionReason']
        self.assertTrue(reason.startswith('Edit denied: '))
        self.assertEqual(result.stderr.decode('utf-8').strip(), reason)
        for fragment in fragments:
            self.assertIn(fragment, reason)
        return reason

    def test_configuration(self):
        entry = _get_hook_entry(_hook_path)
        self.assertEqual(entry['matcher'], r'(?:functions\.)?(?:edit|create|insert|str_replace_editor|apply_patch)')

    def test_inflation_is_denied(self):
        for path, old, new in [
            ('Worker.cs', 'await Task.Delay(100);', 'await Task.Delay(5000);'),
            ('Worker.cs', 'Thread.Sleep(50);', 'Thread.Sleep(1_000);'),
            ('Worker.cs', 'private const int TimeoutMs = 3000;', 'private const int TimeoutMs = 30000;'),
            ('Worker.cs', 'client.Timeout = TimeSpan.FromSeconds(5);', 'client.Timeout = TimeSpan.FromSeconds(60);'),
            ('Tests.cs', '[Fact(Timeout = 1000)]', '[Fact(Timeout = 10000)]'),
            ('worker.py', 'time.sleep(0.1)', 'time.sleep(2)'),
            ('worker.py', '@retry(stop=stop_after_attempt(3))', '@retry(stop=stop_after_attempt(10))'),
            ('worker.py', 'MAX_RETRIES = 3', 'MAX_RETRIES = 5'),
            ('a.test.ts', 'jest.setTimeout(5000);', 'jest.setTimeout(60000);'),
            ('a.test.ts', "await page.waitForSelector('#ok', { timeout: 500 });",
             "await page.waitForSelector('#ok', { timeout: 5000 });"),
            ('a.ts', 'const POLL_INTERVAL = 100ms;', 'const POLL_INTERVAL = 500ms;'),
            ('ci.yml', 'timeout-minutes: 10', 'timeout-minutes: 60'),
            ('a.go', 'ctx, cancel := context.WithTimeout(ctx, 2*time.Second)', 'ctx, cancel := context.WithTimeout(ctx, 20*time.Second)'),
            ('a.py', 'retry(times=3, delay=1)', 'retry(times=5, delay=1)'),
        ]:
            with self.subTest(old=old):
                self.__new_session()
                result = self.__run_call(_edit(path, old, new))
                self.__assert_denied(result, f'{path} would only raise timing values ("{old}" -> "{new}")')

    def test_other_changes_are_allowed(self):
        for path, old, new in [
            # 減らす、増減が混在する、単位が変わる書き換え。
            ('a.py', 'time.sleep(2)', 'time.sleep(0.1)'),
            ('a.py', 'retry(times=3, delay=2)', 'retry(times=5, delay=1)'),
            ('a.ts', 'const TIMEOUT = 5m;', 'const TIMEOUT = 300s;'),
            ('A.cs', 'TimeSpan.FromSeconds(5)', 'TimeSpan.FromMinutes(1)'),
            # 待ち時間と関係のない数値。awaitは待機の語とみなさない。
            ('a.py', 'items = fetch(limit=10)', 'items = fetch(limit=100)'),
            ('a.ts', 'await load(3);', 'await load(5);'),
            # ロジックの変更や行の追加・削除を含む書き換え。
            ('a.py', 'time.sleep(1)\nreturn read()', 'time.sleep(5)\nreturn read_all()'),
            ('a.py', 'time.sleep(1)', 'time.sleep(5)\nwait_until_ready()'),
            ('a.py', 'time.sleep(1)\nassert ready()', 'time.sleep(5)'),
            ('a.py', 'await Task.Delay(100);', 'await WaitUntilReadyAsync(TimeSpan.FromSeconds(5));'),
            # 数値が変わらない書き換え、文書のファイル。
            ('a.py', 'time.sleep(1)', 'time.sleep(1)  '),
            ('README.md', 'timeout: 10', 'timeout: 60'),
        ]:
            with self.subTest(old=old, new=new):
                self.__new_session()
                self.__assert_allowed(self.__run_call(_edit(path, old, new)))

    def test_comments_and_blank_lines_do_not_count_as_logic(self):
        call = _edit('a.py', 'def f():\n    time.sleep(1)\n', 'def f():\n    # CIが遅いため延ばす\n\n    time.sleep(5)\n')
        self.__assert_denied(self.__run_call(call), '"time.sleep(1)" -> "time.sleep(5)"')

    def test_tool_formats(self):
        self.__write_file('a.py', 'import time\n\ntime.sleep(1)\n')
        patch = '*** Begin Patch\n*** Update File: a.py\n import time\n-time.sleep(1)\n+time.sleep(10)\n*** End Patch'
        for name, tool_call in [
            ('json string', {'name': 'functions.edit', 'args': json.dumps(_edit('a.py', 'sleep(1)', 'sleep(10)')['args'])}),
            ('str_replace_editor', {'name': 'str_replace_editor', 'args': {
                'command': 'str_replace', 'path': 'a.py', 'old_str': 'sleep(1)', 'new_str': 'sleep(10)'}}),
            ('apply_patch', {'name': 'apply_patch', 'args': patch}),
            ('create overwrite', {'name': 'create', 'args': {'path': 'a.py', 'file_text': 'import time\n\ntime.sleep(10)\n'}}),
        ]:
            with self.subTest(name=name):
                self.__new_session()
                self.__assert_denied(self.__run_call(tool_call), 'a.py would only raise timing values')

    def test_ignored_calls(self):
        for name, tool_call in [
            ('new file', {'name': 'create', 'args': {'path': 'new.py', 'file_text': 'time.sleep(10)\n'}}),
            ('insert', {'name': 'insert', 'args': {'path': 'a.py', 'insert_line': 1, 'new_str': 'time.sleep(10)'}}),
            ('view', {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': 'a.py', 'old_str': 'sleep(1)', 'new_str': 'sleep(9)'}}),
            ('delete patch', {'name': 'apply_patch', 'args': '*** Begin Patch\n*** Delete File: a.py\n-time.sleep(1)\n*** End Patch'}),
            ('bash', {'name': 'bash', 'args': {'command': "sed -i 's/sleep(1)/sleep(9)/' a.py"}}),
        ]:
            with self.subTest(name=name):
                self.__assert_allowed(self.__run_call(tool_call))
        self.assertFalse(os.path.exists(os.path.join(self.__root, 'ghc-agent-refinement')))

    def test_denies_only_once_per_file(self):
        call = _edit('a.py', 'time.sleep(1)', 'time.sleep(5)')
        self.__assert_denied(self.__run_call(call))
        self.__assert_allowed(self.__run_call(call))
        self.__assert_allowed(self.__run_call(_edit('a.py', 'time.sleep(5)', 'time.sleep(9)')))
        self.__assert_denied(self.__run_call(_edit('b.py', 'time.sleep(1)', 'time.sleep(5)')))
        self.__new_session()
        self.__assert_denied(self.__run_call(call))

    def test_patch_files_are_reported_together_and_snippets_are_limited(self):
        old = ''.join(f'retry_{index} = {index}\n' for index in range(5))
        new = ''.join(f'retry_{index} = {index + 10}\n' for index in range(5))
        patch = (
            '*** Begin Patch\n*** Update File: a.py\n' + ''.join(f'-{line}\n' for line in old.splitlines())
            + ''.join(f'+{line}\n' for line in new.splitlines())
            + '*** Update File: b.py\n-time.sleep(1)\n+time.sleep(2)\n*** End Patch'
        )
        reason = self.__assert_denied(self.__run_call({'name': 'apply_patch', 'args': patch}), 'a.py would', 'b.py would')
        self.assertIn('retry_2', reason)
        self.assertNotIn('retry_3', reason)

    def test_invalid_input(self):
        call = {'toolName': 'edit', 'toolArgs': _edit('a.py', 'sleep(1)', 'sleep(5)')['args']}

        def encode(value):
            return json.dumps(value).encode('utf-8')

        for name, raw in [
            ('empty', b''),
            ('broken json', b'{'),
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
                result = self.__run_process(raw)
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to check timeout inflation:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}}
        for name, content in [
            ('not object', '[]'),
            ('broken json', '{'),
            ('missing excluded', json.dumps({'timingPatterns': ['x']})),
            ('empty timing', json.dumps({'excludedFilePatterns': [], 'timingPatterns': []})),
            ('blank pattern', json.dumps({'excludedFilePatterns': [' '], 'timingPatterns': ['x']})),
            ('invalid excluded regex', json.dumps({'excludedFilePatterns': ['('], 'timingPatterns': ['x']})),
            ('invalid timing regex', json.dumps({'excludedFilePatterns': [], 'timingPatterns': ['(']})),
        ]:
            with self.subTest(name=name):
                definitions_path = os.path.join(self.__root, 'definitions.json')
                with open(definitions_path, 'w', encoding='utf-8', newline='\n') as file:
                    file.write(content)
                result = self.__run_process(valid_input, '--definitions-path', definitions_path)
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to check timeout inflation:', result.stderr)


if __name__ == '__main__':
    _run_tests()
