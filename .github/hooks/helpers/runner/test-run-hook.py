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


script_directory = os.path.dirname(os.path.abspath(__file__))
helpers_directory = os.path.dirname(script_directory)
hooks_directory = os.path.dirname(helpers_directory)
runner_path = os.path.join(script_directory, 'run-hook.py')
state_helper_path = os.path.join(helpers_directory, 'utilities', 'hook-activity-state.py')
RUNNER = '.github/hooks/helpers/runner/run-hook.py'
# Pythonが無い環境でも動く必要があるため、ランナーを通さないhook。
DIRECT_HOOKS = ('block-commands', 'check-python')

# 標準入力をそのまま返し、引数代わりの環境変数で動作を切り替える偽のhook。
FAKE_HOOK = '''import os
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
        self.root = os.path.realpath(temporary.name)
        # ランナーは自身の位置からリポジトリルートとhookの配置を決めるため、同じ構成を一時領域に作る。
        self.repository = os.path.join(self.root, 'repository')
        self.helpers = os.path.join(self.repository, '.github', 'hooks', 'helpers')
        os.makedirs(os.path.join(self.helpers, 'runner'))
        os.makedirs(os.path.join(self.helpers, 'utilities'))
        shutil.copy(runner_path, os.path.join(self.helpers, 'runner', 'run-hook.py'))
        shutil.copy(state_helper_path, os.path.join(self.helpers, 'utilities', 'hook-activity-state.py'))
        self.fake_hook = '.github/hooks/helpers/events/preToolUse/fake-hook/fake-hook.py'
        self.write_file(self.fake_hook, FAKE_HOOK)
        self.temp_directory = os.path.join(self.root, 'temp')
        os.makedirs(self.temp_directory)
        self.session_id = str(uuid.uuid4())

    def write_file(self, relative_path, content):
        path = os.path.join(self.repository, *relative_path.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='') as file:
            file.write(content)

    def run_hook(self, stdin, *arguments, **environment):
        if not arguments:
            arguments = (self.fake_hook,)
        env = os.environ.copy()
        env.update({'TMPDIR': self.temp_directory, 'TMP': self.temp_directory, 'TEMP': self.temp_directory})
        env.update(environment)
        return subprocess.run(
            [sys.executable, os.path.join(self.helpers, 'runner', 'run-hook.py'), *arguments],
            input=stdin, capture_output=True, env=env, check=False,
        )

    def payload(self, **values):
        return json.dumps({'sessionId': self.session_id, **values}).encode('utf-8')

    def recorded_runs(self):
        state_path = os.path.join(
            self.temp_directory, 'ghc-agent-refinement', 'hook-states', 'hook-activity', f'{self.session_id}.sqlite3',
        )
        if not os.path.isfile(state_path):
            return []
        connection = sqlite3.connect(state_path)
        self.addCleanup(connection.close)
        return connection.execute('SELECT event_name, hook_name, fired FROM hook_runs ORDER BY rowid').fetchall()

    def test_relays_input_output_and_exit_code(self):
        stdin = self.payload(text='日本語\r\n')
        result = self.run_hook(stdin, FAKE_STDERR='warning\r\n', FAKE_EXIT='3')
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
                result = self.run_hook(self.payload(), FAKE_MODE=mode)
                self.assertEqual(result.returncode, exit_code)
                self.assertEqual(result.stdout, b'')
                self.assertIn(error, result.stderr)
                self.assertEqual(self.recorded_runs()[-1], ('preToolUse', 'fake-hook', 1 if exit_code else 0))

    def test_text_output_matches_direct_run(self):
        result = self.run_hook(self.payload(), FAKE_MODE='text')
        # Windowsでは改行コードが変換される。
        self.assertEqual(result.stdout, f'日本語{os.linesep}'.encode('utf-8'))

    def test_runs_hook_in_runner_process(self):
        self.write_file('.github/hooks/helpers/events/preToolUse/fake-hook/fake_sibling.py', "VALUE = 'sibling'\n")
        env = os.environ.copy()
        env.update({'TMPDIR': self.temp_directory, 'TMP': self.temp_directory, 'TEMP': self.temp_directory})
        env['FAKE_MODE'] = 'environment'
        process = subprocess.Popen(
            [sys.executable, os.path.join(self.helpers, 'runner', 'run-hook.py'), self.fake_hook],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        stdout, stderr = process.communicate(self.payload())
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout.decode('utf-8').split(), ['sibling', str(process.pid), 'True', '__main__'])

    def test_killing_runner_stops_hook(self):
        # タイムアウトでランナーが終了させられたとき、hookが取り残されて副作用だけを残さないことを確かめる。
        marker = os.path.join(self.root, 'marker')
        env = os.environ.copy()
        env.update({'TMPDIR': self.temp_directory, 'TMP': self.temp_directory, 'TEMP': self.temp_directory})
        env.update({'FAKE_MODE': 'sleep', 'FAKE_MARKER': marker})
        process = subprocess.Popen(
            [sys.executable, os.path.join(self.helpers, 'runner', 'run-hook.py'), self.fake_hook],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        process.stdin.write(self.payload())
        process.stdin.close()
        deadline = time.monotonic() + 10
        while not os.path.exists(marker + '.started') and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(marker + '.started'))
        process.kill()
        process.wait()
        time.sleep(3)
        self.assertFalse(os.path.exists(marker + '.finished'))
        self.assertEqual(self.recorded_runs(), [])

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
                self.run_hook(self.payload(), FAKE_STDOUT=stdout or ' ', FAKE_EXIT=exit_code)
                self.assertEqual(self.recorded_runs()[-1], ('preToolUse', 'fake-hook', 1 if fired else 0))

    def test_skips_recording_without_session_id(self):
        for stdin in (b'', b'{invalid', b'[]', json.dumps({'sessionId': ' '}).encode('utf-8'), b'{"sessionId":"\xff"}'):
            with self.subTest(stdin=stdin):
                result = self.run_hook(stdin, FAKE_STDOUT='{"decision":"block"}')
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'{"decision":"block"}')
                self.assertEqual(self.recorded_runs(), [])

    def test_recording_failure_keeps_hook_result(self):
        result = self.run_hook(
            json.dumps({'sessionId': 'not-a-uuid'}).encode('utf-8'), FAKE_STDOUT='{"decision":"block"}',
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'{"decision":"block"}')
        self.assertIn(b'Failed to record hook run for fake-hook: sessionId must be a valid UUID.', result.stderr)

    def test_rejects_invalid_arguments(self):
        self.write_file('.github/hooks/helpers/events/preToolUse/fake-hook/other.py', FAKE_HOOK)
        self.write_file('.github/hooks/helpers/other/fake-hook/fake-hook.py', FAKE_HOOK)
        cases = (
            ((), 'Usage: run-hook.py'),
            ((self.fake_hook, 'extra'), 'Usage: run-hook.py'),
            (('.github/hooks/helpers/events/preToolUse/fake-hook/other.py',), 'Failed to resolve hook script'),
            (('.github/hooks/helpers/other/fake-hook/fake-hook.py',), 'Failed to resolve hook script'),
            (('.github/hooks/helpers/events/preToolUse/missing/missing.py',), 'Hook script not found'),
        )
        for arguments, expected_error in cases:
            with self.subTest(arguments=arguments):
                if arguments:
                    result = self.run_hook(self.payload(), *arguments)
                else:
                    result = subprocess.run(
                        [sys.executable, os.path.join(self.helpers, 'runner', 'run-hook.py')],
                        input=self.payload(), capture_output=True, check=False,
                    )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(expected_error, result.stderr.decode('utf-8'))
                self.assertEqual(self.recorded_runs(), [])

    def test_get_fired_hooks_filters_by_time_and_event(self):
        state = runpy.run_path(state_helper_path)
        with patch.object(tempfile, 'tempdir', self.temp_directory):
            self.assertEqual(state['get_fired_hooks'](self.session_id, 0, ('agentStop',)), [])
            for started_at_ms, event_name, hook_name, fired in (
                (100, 'preToolUse', 'before-turn', True),
                (200, 'preToolUse', 'z-hook', True),
                (300, 'postToolUse', 'a-hook', True),
                (300, 'postToolUse', 'a-hook', True),
                (400, 'postToolUse', 'passed', False),
                (500, 'agentStop', 'stop-hook', True),
            ):
                state['record_hook_run'](self.session_id, event_name, hook_name, started_at_ms, fired)
            self.assertEqual(state['get_fired_hooks'](self.session_id, 200, ('agentStop',)), ['a-hook', 'z-hook'])
            self.assertEqual(
                state['get_fired_hooks'](self.session_id.upper(), 0, ()), ['a-hook', 'before-turn', 'stop-hook', 'z-hook'],
            )
            for session_id in (None, '', 'not-a-uuid'):
                with self.subTest(session_id=session_id), self.assertRaises(ValueError):
                    state['get_fired_hooks'](session_id, 0, ())

    def test_configuration(self):
        for filename in sorted(name for name in os.listdir(hooks_directory) if name.endswith('.json')):
            with open(os.path.join(hooks_directory, filename), encoding='utf-8') as file:
                configuration = json.load(file)
            for event_name, entries in configuration['hooks'].items():
                for entry in entries:
                    hook_name = os.path.basename(os.path.dirname(entry['bash'].split()[-1]))
                    with self.subTest(event_name=event_name, hook_name=hook_name):
                        relative_path = f'.github/hooks/helpers/events/{event_name}/{hook_name}/{hook_name}.py'
                        if hook_name in DIRECT_HOOKS:
                            self.assertNotIn(RUNNER, entry['bash'])
                            self.assertNotIn(RUNNER, entry['powershell'])
                        else:
                            self.assertEqual(entry['bash'], f'python3 {RUNNER} {relative_path}')
                            self.assertEqual(entry['powershell'], f'python {RUNNER} {relative_path}')


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
    from hook_test_output import run_tests
    run_tests()
