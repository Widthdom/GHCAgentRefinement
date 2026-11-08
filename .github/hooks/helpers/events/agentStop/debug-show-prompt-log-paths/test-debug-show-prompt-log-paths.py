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
        self.__root = temporary.name
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__session_id = str(uuid.uuid4())
        self.__debug_path = os.path.join(self.__root, 'ghc-agent-refinement', 'hook-states', 'debug-mode', f'{self.__session_id}.json')
        os.makedirs(os.path.dirname(self.__debug_path))
        self.__write_debug_state({'enabled': True})
        self.__log_directory = os.path.join(
            self.__root, 'ghc-agent-refinement', 'debug-logs', 'prompt-transformations', self.__session_id,
        )
        os.makedirs(self.__log_directory)
        self.__log_path = self.__write_log('100-user.md')
        self.__transcript = os.path.join(self.__root, 'events.jsonl')
        self.__events = [
            self.__event('userPromptSubmitted', 90),
            self.__event('userPromptTransformed', 100),
            {'type': 'user.message', 'data': {'content': '?? request'}},
            {'type': 'assistant.message', 'data': {'content': 'Response body'}},
            self.__event('agentStop', 110),
        ]
        self.__write_events()
        self.__payload = {'sessionId': self.__session_id, 'transcriptPath': self.__transcript, 'timestamp': 110}

    def __event(self, kind, timestamp, **extra_input):
        return {'type': 'hook.start', 'data': {'hookType': kind, 'input': {
            'sessionId': self.__session_id, 'timestamp': timestamp, **extra_input,
        }}}

    def __write_events(self):
        with open(self.__transcript, 'w', encoding='utf-8', newline='') as file:
            for event in self.__events:
                file.write(json.dumps(event, ensure_ascii=False) + '\n')

    def __write_debug_state(self, state):
        with open(self.__debug_path, 'w', encoding='utf-8', newline='\n') as file:
            json.dump(state, file)

    def __write_log(self, name):
        path = os.path.join(self.__log_directory, name)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write('report')
        return path

    def __invoke(self, payload=None, raw=None, script=_hook_path):
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload).encode('utf-8')
        return subprocess.run(
            [sys.executable, script], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.__environment, timeout=10,
        )

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __displayed_paths(self, result):
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

    def test_displays_current_log_as_progress(self):
        self.assertEqual(self.__displayed_paths(self.__invoke()), [self.__log_path])

    def test_repeated_stops_show_original_logs_again(self):
        self.assertEqual(self.__displayed_paths(self.__invoke()), [self.__log_path])
        self.__write_log('120-internal.md')
        self.__events.extend([self.__event('userPromptTransformed', 120), self.__event('agentStop', 130)])
        self.__write_events()
        result = self.__invoke({**self.__payload, 'timestamp': 130})
        self.assertEqual(self.__displayed_paths(result), [self.__log_path])

    def test_new_request_does_not_show_old_logs(self):
        self.__events.extend([self.__event('userPromptSubmitted', 190), self.__event('agentStop', 210)])
        self.__write_events()
        payload = {**self.__payload, 'timestamp': 210}
        self.__assert_silent(self.__invoke(payload))
        self.__events.insert(-1, self.__event('userPromptTransformed', 200))
        new_path = self.__write_log('200-new.md')
        self.__write_events()
        self.assertEqual(self.__displayed_paths(self.__invoke(payload)), [new_path])

    def test_unrelated_sessions_and_subagents_are_ignored(self):
        self.__write_log('101-child.md')
        self.__write_log('102-other.md')
        self.__events[2:2] = [
            self.__event('userPromptTransformed', 101, agentId='child'),
            self.__event('userPromptTransformed', 102, sessionId=str(uuid.uuid4())),
        ]
        self.__write_events()
        self.assertEqual(self.__displayed_paths(self.__invoke()), [self.__log_path])
        self.__assert_silent(self.__invoke({**self.__payload, 'agentId': 'child'}))

    def test_missing_or_disabled_debug_mode_is_silent(self):
        self.__assert_silent(self.__invoke({**self.__payload, 'sessionId': str(uuid.uuid4())}))
        for state in ({}, {'enabled': False}, {'enabled': None}, {'enabled': 1}, {'enabled': 'true'}):
            with self.subTest(state=state):
                self.__write_debug_state(state)
                self.__assert_silent(self.__invoke())

    def test_missing_logs_are_silent_and_duplicate_timestamps_keep_all_files(self):
        another = self.__write_log('100-another.md')
        self.assertEqual(self.__displayed_paths(self.__invoke()), sorted([self.__log_path, another]))
        os.unlink(self.__log_path)
        os.unlink(another)
        os.makedirs(os.path.join(self.__log_directory, '100-directory.md'))
        self.__assert_silent(self.__invoke())

    def test_invalid_input_reports_error_without_progress(self):
        invalid = [b'', b' ', b'{', b'null', b'[]', b'42', b'{"sessionId":"\xff"}']
        for changes in (
            {'sessionId': None}, {'sessionId': ''}, {'sessionId': '../bad'}, {'sessionId': []},
            {'transcriptPath': None}, {'transcriptPath': ''}, {'transcriptPath': []},
            {'transcriptPath': 'relative'}, {'transcriptPath': os.path.join(self.__root, 'missing')},
        ):
            invalid.append(json.dumps({**self.__payload, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to show prompt log paths:', result.stderr)

    def test_invalid_debug_state_and_transcript_report_error(self):
        for raw in (b'{', b'null', b'[]', b'{"enabled":"\xff"}'):
            with self.subTest(state=raw):
                with open(self.__debug_path, 'wb') as file:
                    file.write(raw)
                result = self.__invoke()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
        self.__write_debug_state({'enabled': True})
        for event in ([], {'type': 'hook.start', 'data': []}):
            with self.subTest(event=event):
                self.__events.insert(0, event)
                self.__write_events()
                result = self.__invoke()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.__events.pop(0)

    def test_wait_for_current_stop_handles_partial_writes_and_times_out(self):
        wait_for_stop = runpy.run_path(_hook_path)['_wait_for_stop']
        self.__events = [self.__event('agentStop', 110, agentId='child')]
        self.__write_events()
        with patch('time.monotonic', side_effect=[0, 5]), patch('time.sleep') as sleep:
            with self.assertRaisesRegex(TimeoutError, 'not recorded within 5 seconds'):
                wait_for_stop(self.__transcript, self.__session_id, 110)
            sleep.assert_not_called()
        with open(self.__transcript, 'a', encoding='utf-8', newline='\n') as file:
            file.write('{"type":')
        self.__events = [self.__event('agentStop', 110)]
        with patch('time.sleep', side_effect=lambda _: self.__write_events()) as sleep:
            wait_for_stop(self.__transcript, self.__session_id, 110)
        sleep.assert_called_once_with(0.05)

    def test_logger_and_reporter_work_without_footer_files(self):
        # 共通utilitiesと新規hookだけをコピーし、既存フッターがなくても実行できることを確認する。
        copied_hooks = os.path.join(self.__root, 'repository', '.github', 'hooks')
        reporter_directory = os.path.join(copied_hooks, 'helpers', 'events', 'agentStop', 'debug-show-prompt-log-paths')
        logger_directory = os.path.join(copied_hooks, 'helpers', 'events', 'userPromptTransformed', 'debug-log-transformed-prompt')
        utilities = os.path.join(copied_hooks, 'helpers', 'utilities')
        for directory in (reporter_directory, logger_directory):
            os.makedirs(directory)
        for name in ('debug-show-prompt-log-paths.py', 'get-prompt-log-paths.py'):
            shutil.copyfile(os.path.join(_script_directory, name), os.path.join(reporter_directory, name))
        for name in ('get-debug-mode', 'get-state-path', 'read-hook-input', 'read-jsonl-objects'):
            os.makedirs(os.path.join(utilities, name))
            shutil.copyfile(os.path.join(_hooks_directory, 'helpers', 'utilities', name, f'{name}.py'),
                            os.path.join(utilities, name, f'{name}.py'))
        logger = 'debug-log-transformed-prompt.py'
        shutil.copyfile(os.path.join(_hooks_directory, 'helpers', 'events', 'userPromptTransformed',
                                    'debug-log-transformed-prompt', logger), os.path.join(logger_directory, logger))
        os.unlink(self.__log_path)
        result = self.__invoke({
            'sessionId': self.__session_id, 'timestamp': 100, 'cwd': self.__root,
            'prompt': '?? request', 'transformedPrompt': 'Runtime context\n?? request',
        }, script=os.path.join(logger_directory, logger))
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        self.assertEqual(json.loads(result.stdout), {})
        result = self.__invoke(script=os.path.join(reporter_directory, 'debug-show-prompt-log-paths.py'))
        paths = self.__displayed_paths(result)
        self.assertEqual(len(paths), 1)
        self.assertTrue(os.path.isfile(paths[0]))


if __name__ == '__main__':
    run_tests()
