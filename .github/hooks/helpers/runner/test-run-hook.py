import json
import os
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch


_script_directory = os.path.dirname(os.path.abspath(__file__))
_helpers_directory = os.path.dirname(_script_directory)
_hooks_directory = os.path.dirname(_helpers_directory)
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_runner_path = os.path.join(_script_directory, 'run-hook.py')
_state_helper_path = os.path.join(_helpers_directory, 'utilities', 'hook-activity-state', 'hook-activity-state.py')
_COPIED_UTILITIES = ('hook-activity-state', 'get-state-path')

# 標準入力をそのまま返し、引数代わりの環境変数で動作を切り替える偽のhook。
_FAKE_HOOK = '''import os
import sys
import time

mode = os.environ.get('FAKE_MODE', '')
if mode == 'raise':
    raise RuntimeError('broken hook')
if mode == 'exit-message':
    sys.exit('exit message')
if mode == 'exit-none':
    sys.exit()
if mode == 'text':
    sys.stdout.reconfigure(encoding='utf-8')
    print('日本語')
    sys.exit(0)
if mode == 'environment':
    import fake_sibling
    print(fake_sibling.VALUE, os.getpid(), sys.argv == [__file__], __name__)
    sys.exit(0)
if mode == 'sleep':
    marker = os.environ['FAKE_MARKER']
    open(marker + '.started', 'w').close()
    time.sleep(2)
    open(marker + '.finished', 'w').close()
    sys.exit(0)
data = sys.stdin.buffer.read()
sys.stdout.buffer.write(os.environ.get('FAKE_STDOUT', '').encode('utf-8') or data)
sys.stderr.buffer.write(os.environ.get('FAKE_STDERR', '').encode('utf-8'))
sys.exit(int(os.environ.get('FAKE_EXIT', '0')))
'''


class RunHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-run-hook-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        # ランナーは自身の位置からリポジトリルートとhookの配置を決めるため、同じ構成を一時領域に作る。
        self.__repository = os.path.join(self.__root, 'repository')
        self.__helpers = os.path.join(self.__repository, '.github', 'hooks', 'helpers')
        os.makedirs(os.path.join(self.__helpers, 'runner'))
        shutil.copy(_runner_path, os.path.join(self.__helpers, 'runner', 'run-hook.py'))
        # 記録の読み書きに使うutilityと、それが読み込むutilityを複製する。
        for utility in _COPIED_UTILITIES:
            os.makedirs(os.path.join(self.__helpers, 'utilities', utility))
            shutil.copy(
                os.path.join(_helpers_directory, 'utilities', utility, f'{utility}.py'),
                os.path.join(self.__helpers, 'utilities', utility, f'{utility}.py'),
            )
        self.__fake_hook = '.github/hooks/helpers/events/preToolUse/fake-hook/fake-hook.py'
        self.__write_file(self.__fake_hook, _FAKE_HOOK)
        self.__temp_directory = os.path.join(self.__root, 'temp')
        os.makedirs(self.__temp_directory)
        self.__session_id = str(uuid.uuid4())

    def __write_file(self, relative_path, content):
        path = os.path.join(self.__repository, *relative_path.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='') as file:
            file.write(content)

    def __environment(self, **values):
        # 記録を実際の一時フォルダーに残さないよう、一時フォルダーを差し替える。
        return dict(os.environ, TMPDIR=self.__temp_directory, TMP=self.__temp_directory, TEMP=self.__temp_directory, **values)

    def __run_runner(self, stdin, arguments, **environment):
        return subprocess.run(
            [sys.executable, os.path.join(self.__helpers, 'runner', 'run-hook.py'), *arguments],
            input=stdin, capture_output=True, env=self.__environment(**environment), check=False, timeout=30,
        )

    def __run_hook(self, stdin, *arguments, **environment):
        return self.__run_runner(stdin, arguments or (self.__fake_hook,), **environment)

    def __payload(self, **values):
        return json.dumps({'sessionId': self.__session_id, **values}).encode('utf-8')

    def __recorded_runs(self):
        state_path = os.path.join(
            self.__temp_directory, 'ghc-agent-refinement', 'hook-states', 'hook-activity', f'{self.__session_id}.sqlite3',
        )
        if not os.path.isfile(state_path):
            return []
        connection = sqlite3.connect(state_path)
        self.addCleanup(connection.close)
        return connection.execute('SELECT event_name, hook_name, fired FROM hook_runs ORDER BY rowid').fetchall()

    def test_relays_input_output_and_exit_code(self):
        stdin = self.__payload(text='日本語\r\n')
        result = self.__run_hook(stdin, FAKE_STDERR='warning\r\n', FAKE_EXIT='3')
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, stdin)
        self.assertEqual(result.stderr, 'warning\r\n'.encode('utf-8'))

    def test_emulates_interpreter_exit(self):
        cases = (
            ('raise', 1, b'RuntimeError: broken hook'),
            ('exit-message', 1, b'exit message'),
            ('exit-none', 0, b''),
        )
        for mode, exit_code, error in cases:
            with self.subTest(mode=mode):
                result = self.__run_hook(self.__payload(), FAKE_MODE=mode)
                self.assertEqual(result.returncode, exit_code)
                self.assertEqual(result.stdout, b'')
                self.assertIn(error, result.stderr)
                self.assertEqual(self.__recorded_runs()[-1], ('preToolUse', 'fake-hook', 1 if exit_code else 0))

    def test_runs_repository_specific_hook(self):
        repository_hook = '.github/hooks/helpers/-repo-events/preToolUse/repo-hook/repo-hook.py'
        self.__write_file(repository_hook, _FAKE_HOOK)
        stdin = self.__payload()
        result = self.__run_hook(stdin, repository_hook)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, stdin, b''))
        self.assertEqual(self.__recorded_runs(), [('preToolUse', 'repo-hook', 1)])

    def test_text_output_matches_direct_run(self):
        result = self.__run_hook(self.__payload(), FAKE_MODE='text')
        # Windowsでは改行コードが変換される。
        self.assertEqual(result.stdout, f'日本語{os.linesep}'.encode('utf-8'))

    def test_runs_hook_in_runner_process(self):
        self.__write_file('.github/hooks/helpers/events/preToolUse/fake-hook/fake_sibling.py', "VALUE = 'sibling'\n")
        process = subprocess.Popen(
            [sys.executable, os.path.join(self.__helpers, 'runner', 'run-hook.py'), self.__fake_hook],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.__environment(FAKE_MODE='environment'),
        )
        stdout, stderr = process.communicate(self.__payload(), timeout=30)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout.decode('utf-8').split(), ['sibling', str(process.pid), 'True', '__main__'])

    def test_killing_runner_stops_hook(self):
        # タイムアウトでランナーが終了させられたとき、hookが取り残されて副作用だけを残さないことを確かめる。
        marker = os.path.join(self.__root, 'marker')
        process = subprocess.Popen(
            [sys.executable, os.path.join(self.__helpers, 'runner', 'run-hook.py'), self.__fake_hook],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=self.__environment(FAKE_MODE='sleep', FAKE_MARKER=marker),
        )
        process.stdin.write(self.__payload())
        process.stdin.close()
        deadline = time.monotonic() + 10
        while not os.path.exists(marker + '.started') and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(marker + '.started'))
        process.kill()
        process.wait()
        time.sleep(3)
        self.assertFalse(os.path.exists(marker + '.finished'))
        self.assertEqual(self.__recorded_runs(), [])

    def test_records_whether_hook_fired(self):
        cases = (
            ('', '0', False),
            (' \r\n', '0', False),
            ('{}\n', '0', False),
            ('{"permissionDecision":"deny"}', '0', True),
            ('not json', '0', True),
            (' ', '2', True),
        )
        for stdout, exit_code, fired in cases:
            with self.subTest(stdout=stdout, exit_code=exit_code):
                self.__run_hook(self.__payload(), FAKE_STDOUT=stdout or ' ', FAKE_EXIT=exit_code)
                self.assertEqual(self.__recorded_runs()[-1], ('preToolUse', 'fake-hook', 1 if fired else 0))

    def test_skips_recording_without_session_id(self):
        for stdin in (b'', b'{invalid', b'[]', json.dumps({'sessionId': ' '}).encode('utf-8'), b'{"sessionId":"\xff"}'):
            with self.subTest(stdin=stdin):
                result = self.__run_hook(stdin, FAKE_STDOUT='{"decision":"block"}')
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'{"decision":"block"}')
                self.assertEqual(self.__recorded_runs(), [])

    def test_recording_failure_keeps_hook_result(self):
        result = self.__run_hook(
            json.dumps({'sessionId': 'not-a-uuid'}).encode('utf-8'), FAKE_STDOUT='{"decision":"block"}',
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'{"decision":"block"}')
        self.assertIn(b'Failed to record hook run for fake-hook: sessionId must be a valid UUID.', result.stderr)

    def test_rejects_invalid_arguments(self):
        self.__write_file('.github/hooks/helpers/events/preToolUse/fake-hook/other.py', _FAKE_HOOK)
        self.__write_file('.github/hooks/helpers/other/fake-hook/fake-hook.py', _FAKE_HOOK)
        self.__write_file('.github/hooks/-repo-events/preToolUse/fake-hook/fake-hook.py', _FAKE_HOOK)
        cases = (
            (('.github/hooks/-repo-events/preToolUse/fake-hook/fake-hook.py',), 'Failed to resolve hook script'),
            ((), 'Usage: run-hook.py'),
            ((self.__fake_hook, 'extra'), 'Usage: run-hook.py'),
            (('.github/hooks/helpers/events/preToolUse/fake-hook/other.py',), 'Failed to resolve hook script'),
            (('.github/hooks/helpers/other/fake-hook/fake-hook.py',), 'Failed to resolve hook script'),
            (('.github/hooks/helpers/events/preToolUse/missing/missing.py',), 'Hook script not found'),
        )
        for arguments, expected_error in cases:
            with self.subTest(arguments=arguments):
                result = self.__run_runner(self.__payload(), arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(expected_error, result.stderr.decode('utf-8'))
                self.assertEqual(self.__recorded_runs(), [])

    def test_get_fired_hooks_filters_by_time_and_event(self):
        state = runpy.run_path(_state_helper_path)
        with patch.object(tempfile, 'tempdir', self.__temp_directory):
            self.assertEqual(state['get_fired_hooks'](self.__session_id, 0, ('agentStop',)), [])
            for started_at_ms, event_name, hook_name, fired in (
                (100, 'preToolUse', 'before-turn', True),
                (200, 'preToolUse', 'z-hook', True),
                (300, 'postToolUse', 'a-hook', True),
                (300, 'postToolUse', 'a-hook', True),
                (400, 'postToolUse', 'passed', False),
                (500, 'agentStop', 'stop-hook', True),
            ):
                state['record_hook_run'](self.__session_id, event_name, hook_name, started_at_ms, fired)
            self.assertEqual(state['get_fired_hooks'](self.__session_id, 200, ('agentStop',)), ['a-hook', 'z-hook'])
            self.assertEqual(
                state['get_fired_hooks'](self.__session_id.upper(), 0, ()), ['a-hook', 'before-turn', 'stop-hook', 'z-hook'],
            )
            for session_id in (None, '', 'not-a-uuid'):
                with self.subTest(session_id=session_id), self.assertRaises(ValueError):
                    state['get_fired_hooks'](session_id, 0, ())


if __name__ == '__main__':
    _run_tests()
