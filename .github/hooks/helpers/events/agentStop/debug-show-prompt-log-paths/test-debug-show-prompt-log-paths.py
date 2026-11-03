import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'debug-show-prompt-log-paths.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class DebugShowPromptLogPathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc prompt-path [日本語] ')
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.session_id = str(uuid.uuid4())
        self.debug_path = os.path.join(self.root, 'ghc-agent-refinement', 'hook-states', 'debug-mode', f'{self.session_id}.json')
        os.makedirs(os.path.dirname(self.debug_path))
        self.write_debug_state({'enabled': True})
        self.log_directory = os.path.join(
            self.root, 'ghc-agent-refinement', 'debug-logs', 'prompt-transformations', self.session_id,
        )
        os.makedirs(self.log_directory)
        self.log_path = self.write_log('100-user.md')
        self.transcript = os.path.join(self.root, 'events.jsonl')
        self.events = [
            self.event('userPromptSubmitted', 90),
            self.event('userPromptTransformed', 100),
            {'type': 'user.message', 'data': {'content': '?? request'}},
            {'type': 'assistant.message', 'data': {'content': 'Response body'}},
            self.event('agentStop', 110),
        ]
        self.write_events()
        self.payload = {'sessionId': self.session_id, 'transcriptPath': self.transcript, 'timestamp': 110}

    def event(self, kind, timestamp, **extra_input):
        return {'type': 'hook.start', 'data': {'hookType': kind, 'input': {
            'sessionId': self.session_id, 'timestamp': timestamp, **extra_input,
        }}}

    def write_events(self):
        with open(self.transcript, 'w', encoding='utf-8', newline='') as file:
            for event in self.events:
                file.write(json.dumps(event, ensure_ascii=False) + '\n')

    def write_debug_state(self, state):
        with open(self.debug_path, 'w', encoding='utf-8') as file:
            json.dump(state, file)

    def write_log(self, name):
        path = os.path.join(self.log_directory, name)
        with open(path, 'w', encoding='utf-8') as file:
            file.write('report')
        return path

    def invoke(self, payload=None, raw=None, script=_hook_path):
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload).encode('utf-8')
        return subprocess.run(
            [sys.executable, script], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.environment, timeout=10,
        )

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def displayed_paths(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = [json.loads(line) for line in result.stdout.decode('utf-8').splitlines()]
        self.assertGreaterEqual(len(output), 2)
        self.assertEqual(output[-1], {})  # 応答継続・本文修正を要求しない。
        paths = []
        for message in output[:-1]:
            self.assertEqual(set(message), {'type', 'message'})
            self.assertEqual(message['type'], 'progress')
            prefix = 'Prompt transformation log: '
            self.assertTrue(message['message'].startswith(prefix))
            paths.append(message['message'][len(prefix):])
        return paths

    def test_configuration_is_sorted(self):
        with open(os.path.join(_hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        entries = configuration['hooks']['agentStop']
        commands = [entry['bash'] for entry in entries]
        self.assertEqual(commands, sorted(commands))
        relative = '.github/hooks/helpers/events/agentStop/debug-show-prompt-log-paths/debug-show-prompt-log-paths.py'
        self.assertIn({'type': 'command', 'bash': f'python3 .github/hooks/helpers/runner/run-hook.py {relative}', 'powershell': f'python .github/hooks/helpers/runner/run-hook.py {relative}'}, entries)

    def test_displays_current_log_as_progress(self):
        self.assertEqual(self.displayed_paths(self.invoke()), [self.log_path])

    def test_repeated_stops_show_original_logs_again(self):
        self.assertEqual(self.displayed_paths(self.invoke()), [self.log_path])
        self.write_log('120-internal.md')
        self.events.extend([self.event('userPromptTransformed', 120), self.event('agentStop', 130)])
        self.write_events()
        result = self.invoke({**self.payload, 'timestamp': 130})
        self.assertEqual(self.displayed_paths(result), [self.log_path])

    def test_new_request_does_not_show_old_logs(self):
        self.events.extend([self.event('userPromptSubmitted', 190), self.event('agentStop', 210)])
        self.write_events()
        payload = {**self.payload, 'timestamp': 210}
        self.assert_silent(self.invoke(payload))
        self.events.insert(-1, self.event('userPromptTransformed', 200))
        new_path = self.write_log('200-new.md')
        self.write_events()
        self.assertEqual(self.displayed_paths(self.invoke(payload)), [new_path])

    def test_unrelated_sessions_and_subagents_are_ignored(self):
        self.write_log('101-child.md')
        self.write_log('102-other.md')
        self.events[2:2] = [
            self.event('userPromptTransformed', 101, agentId='child'),
            self.event('userPromptTransformed', 102, sessionId=str(uuid.uuid4())),
        ]
        self.write_events()
        self.assertEqual(self.displayed_paths(self.invoke()), [self.log_path])
        self.assert_silent(self.invoke({**self.payload, 'agentId': 'child'}))

    def test_missing_or_disabled_debug_mode_is_silent(self):
        self.assert_silent(self.invoke({**self.payload, 'sessionId': str(uuid.uuid4())}))
        for state in ({}, {'enabled': False}, {'enabled': None}, {'enabled': 1}, {'enabled': 'true'}):
            with self.subTest(state=state):
                self.write_debug_state(state)
                self.assert_silent(self.invoke())

    def test_missing_logs_are_silent_and_duplicate_timestamps_keep_all_files(self):
        another = self.write_log('100-another.md')
        self.assertEqual(self.displayed_paths(self.invoke()), sorted([self.log_path, another]))
        os.unlink(self.log_path)
        os.unlink(another)
        os.makedirs(os.path.join(self.log_directory, '100-directory.md'))
        self.assert_silent(self.invoke())

    def test_invalid_input_reports_error_without_progress(self):
        invalid = [b'', b' ', b'{', b'null', b'[]', b'42', b'{"sessionId":"\xff"}']
        for changes in (
            {'sessionId': None}, {'sessionId': ''}, {'sessionId': '../bad'}, {'sessionId': []},
            {'transcriptPath': None}, {'transcriptPath': ''}, {'transcriptPath': []},
            {'transcriptPath': 'relative'}, {'transcriptPath': os.path.join(self.root, 'missing')},
        ):
            invalid.append(json.dumps({**self.payload, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to show prompt log paths:', result.stderr)

    def test_invalid_debug_state_and_transcript_report_error(self):
        for raw in (b'{', b'null', b'[]', b'{"enabled":"\xff"}'):
            with self.subTest(state=raw):
                with open(self.debug_path, 'wb') as file:
                    file.write(raw)
                result = self.invoke()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
        self.write_debug_state({'enabled': True})
        for event in ([], {'type': 'hook.start', 'data': []}):
            with self.subTest(event=event):
                self.events.insert(0, event)
                self.write_events()
                result = self.invoke()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.events.pop(0)

    def test_wait_for_current_stop_handles_partial_writes_and_times_out(self):
        wait_for_stop = runpy.run_path(_hook_path)['_wait_for_stop']
        self.events = [self.event('agentStop', 110, agentId='child')]
        self.write_events()
        with patch('time.monotonic', side_effect=[0, 5]), patch('time.sleep') as sleep:
            with self.assertRaisesRegex(TimeoutError, 'not recorded within 5 seconds'):
                wait_for_stop(self.transcript, self.session_id, 110)
            sleep.assert_not_called()
        with open(self.transcript, 'a', encoding='utf-8') as file:
            file.write('{"type":')
        self.events = [self.event('agentStop', 110)]
        with patch('time.sleep', side_effect=lambda _: self.write_events()) as sleep:
            wait_for_stop(self.transcript, self.session_id, 110)
        sleep.assert_called_once_with(0.05)

    def test_logger_and_reporter_work_without_footer_files(self):
        # 共通utilitiesと新規hookだけをコピーし、既存フッターがなくても実行できることを確認する。
        copied_hooks = os.path.join(self.root, 'repository', '.github', 'hooks')
        reporter_directory = os.path.join(copied_hooks, 'helpers', 'events', 'agentStop', 'debug-show-prompt-log-paths')
        logger_directory = os.path.join(copied_hooks, 'helpers', 'events', 'userPromptTransformed', 'debug-log-transformed-prompt')
        utilities = os.path.join(copied_hooks, 'helpers', 'utilities')
        for directory in (reporter_directory, logger_directory):
            os.makedirs(directory)
        for name in ('debug-show-prompt-log-paths.py', 'get-prompt-log-paths.py'):
            shutil.copyfile(os.path.join(_script_directory, name), os.path.join(reporter_directory, name))
        for name in ('get-debug-mode', 'read-jsonl-objects'):
            os.makedirs(os.path.join(utilities, name))
            shutil.copyfile(os.path.join(_hooks_directory, 'helpers', 'utilities', name, f'{name}.py'),
                            os.path.join(utilities, name, f'{name}.py'))
        logger = 'debug-log-transformed-prompt.py'
        shutil.copyfile(os.path.join(_hooks_directory, 'helpers', 'events', 'userPromptTransformed',
                                    'debug-log-transformed-prompt', logger), os.path.join(logger_directory, logger))
        os.unlink(self.log_path)
        result = self.invoke({
            'sessionId': self.session_id, 'timestamp': 100, 'cwd': self.root,
            'prompt': '?? request', 'transformedPrompt': 'Runtime context\n?? request',
        }, script=os.path.join(logger_directory, logger))
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        self.assertEqual(json.loads(result.stdout), {})
        result = self.invoke(script=os.path.join(reporter_directory, 'debug-show-prompt-log-paths.py'))
        paths = self.displayed_paths(result)
        self.assertEqual(len(paths), 1)
        self.assertTrue(os.path.isfile(paths[0]))


if __name__ == '__main__':
    run_tests()
