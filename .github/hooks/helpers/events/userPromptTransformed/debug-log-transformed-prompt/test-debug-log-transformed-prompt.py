import glob
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'debug-log-transformed-prompt.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class DebugLogTransformedPromptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc prompt-log 日本語 ')
        self.addCleanup(temporary.cleanup)
        self.__root = temporary.name
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__session_id = str(uuid.uuid4())
        self.__debug_directory = os.path.join(self.__root, 'ghc-agent-refinement', 'hook-states', 'debug-mode')
        self.__log_root = os.path.join(self.__root, 'ghc-agent-refinement', 'debug-logs', 'prompt-transformations')
        self.__log_directory = os.path.join(self.__log_root, self.__session_id)
        self.__known_paths = set()
        self.__payload = {
            'sessionId': self.__session_id,
            'timestamp': 1793000000000,
            'cwd': self.__root,
            'prompt': '?? 調べて\n',
            'transformedPrompt': '<current_datetime>2026-10-27</current_datetime>\n?? 調べて\n',
        }
        self.__write_debug_state({'enabled': True})

    def __write_debug_state(self, state, session_id=None):
        os.makedirs(self.__debug_directory, exist_ok=True)
        path = os.path.join(self.__debug_directory, f'{session_id or self.__session_id}.json')
        with open(path, 'w', encoding='utf-8', newline='') as file:
            json.dump(state, file)
        return path

    def __invoke(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.__environment, timeout=10,
        )

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __logged_path(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(json.loads(result.stdout.decode('utf-8')), {})
        # 保存時にはprogressを表示しない。各呼び出しが新しく作ったログを確認する。
        paths = set(glob.glob(os.path.join(glob.escape(self.__log_root), '*', '*.md')))
        created = paths - self.__known_paths
        self.assertEqual(len(created), 1)
        self.__known_paths = paths
        path = created.pop()
        self.assertTrue(os.path.isabs(path))
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(path.endswith('.md'))
        return path

    def __read_log(self, path):
        with open(path, 'r', encoding='utf-8', newline='') as file:
            return file.read()

    def test_records_prompts_and_added_runtime_context(self):
        path = self.__logged_path(self.__invoke())
        self.assertEqual(os.path.dirname(path), self.__log_directory)
        report = self.__read_log(path)
        self.assertLess(report.index('## Difference'), report.index('## User prompt'))
        self.assertIn('## User prompt (`prompt`)\n\n```text\n?? 調べて\n```', report)
        self.assertIn(self.__payload['transformedPrompt'], report)
        self.assertIn('--- prompt\n+++ transformedPrompt\n', report)
        self.assertIn('+<current_datetime>2026-10-27</current_datetime>\n', report)
        self.assertIn(' ?? 調べて\n', report)
        event_json = report.split('```json\n', 1)[1].split('\n```', 1)[0]
        self.assertEqual(json.loads(event_json), {
            'sessionId': self.__session_id, 'timestamp': self.__payload['timestamp'], 'cwd': self.__root,
        })
        with open(path, 'rb') as file:
            self.assertFalse(file.read().startswith(b'\xef\xbb\xbf'))

    def test_records_unchanged_prompt(self):
        payload = {**self.__payload, 'transformedPrompt': self.__payload['prompt']}
        report = self.__read_log(self.__logged_path(self.__invoke(payload)))
        self.assertIn('No differences.', report)
        self.assertEqual(report.count('```text\n?? 調べて\n```'), 2)

    def test_missing_or_disabled_debug_mode_does_not_create_logs(self):
        self.__assert_silent(self.__invoke({**self.__payload, 'sessionId': str(uuid.uuid4())}))
        for state in ({}, {'enabled': False}, {'enabled': None}, {'enabled': 1}, {'enabled': 'true'}):
            with self.subTest(state=state):
                self.__write_debug_state(state)
                self.__assert_silent(self.__invoke())
                self.assertFalse(os.path.exists(self.__log_root))

    def test_existing_debug_switch_controls_logging(self):
        switch_path = os.path.join(
            _hooks_directory, 'helpers', 'events', 'userPromptSubmitted', 'set-debug-mode', 'set-debug-mode.py',
        )
        for prompt, expected_enabled in (('?? 最初の依頼', True), ('次の通常の依頼', False)):
            with self.subTest(prompt=prompt):
                switch = subprocess.run(
                    [sys.executable, switch_path],
                    input=json.dumps({'sessionId': self.__session_id, 'prompt': prompt}).encode('utf-8'),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
                )
                self.__assert_silent(switch)
                result = self.__invoke({**self.__payload, 'prompt': prompt, 'transformedPrompt': prompt})
                if expected_enabled:
                    self.__logged_path(result)
                else:
                    self.__assert_silent(result)
        self.assertEqual(len(glob.glob(os.path.join(self.__log_directory, '*.md'))), 1)

    def test_sessions_are_isolated_and_uuid_is_normalized(self):
        other_session_id = str(uuid.uuid4())
        other = {**self.__payload, 'sessionId': other_session_id}
        self.__assert_silent(self.__invoke(other))
        padded = {**self.__payload, 'sessionId': ' {' + self.__session_id.upper() + '} '}
        first_path = self.__logged_path(self.__invoke(padded))
        self.assertEqual(os.path.dirname(first_path), self.__log_directory)
        self.__write_debug_state({'enabled': True}, other_session_id)
        other_path = self.__logged_path(self.__invoke(other))
        self.assertEqual(os.path.dirname(other_path), os.path.join(self.__log_root, other_session_id))

    def test_subagents_do_not_create_logs(self):
        self.__assert_silent(self.__invoke({**self.__payload, 'agentId': 'child-agent'}))
        self.assertFalse(os.path.exists(self.__log_root))

    def test_same_timestamp_keeps_separate_logs(self):
        first_path = self.__logged_path(self.__invoke())
        before = self.__read_log(first_path)
        second = {**self.__payload, 'prompt': '?? 次の依頼', 'transformedPrompt': '次の変換後の文章'}
        second_path = self.__logged_path(self.__invoke(second))
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(self.__read_log(first_path), before)
        self.assertIn(second['transformedPrompt'], self.__read_log(second_path))
        self.assertFalse(glob.glob(os.path.join(self.__log_directory, '*.tmp')))

    def test_markdown_fences_in_prompt_are_preserved(self):
        prompt = '??\n`````\n# This is prompt content\n`````\n'
        payload = {**self.__payload, 'prompt': prompt, 'transformedPrompt': 'Added context\n' + prompt}
        report = self.__read_log(self.__logged_path(self.__invoke(payload)))
        self.assertIn('``````text\n' + prompt + '``````\n', report)
        self.assertIn('``````diff\n', report)

    def test_diff_marks_missing_final_newlines(self):
        payload = {**self.__payload, 'prompt': '?? old', 'transformedPrompt': '?? new'}
        report = self.__read_log(self.__logged_path(self.__invoke(payload)))
        self.assertIn('-?? old\n\\ No newline at end of file\n', report)
        self.assertIn('+?? new\n\\ No newline at end of file\n', report)

    def test_empty_strings_and_original_line_endings_are_recorded(self):
        for prompt, transformed in (('', ''), ('?? 一行目\r\n二行目\r\n', '?? 一行目\n二行目\n'), (' \t', ' \t')):
            with self.subTest(prompt=prompt, transformed=transformed):
                payload = {**self.__payload, 'prompt': prompt, 'transformedPrompt': transformed}
                report = self.__read_log(self.__logged_path(self.__invoke(payload)))
                self.assertIn('```text\n' + prompt, report)
                self.assertIn('```text\n' + transformed, report)
                if prompt != transformed:
                    self.assertIn('-?? 一行目\r\n', report)
                    self.assertIn('+?? 一行目\n', report)

    def test_invalid_payload_never_creates_logs_or_progress(self):
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'true', b'42', b'{"prompt":"\xff"}']
        for field in ('sessionId', 'timestamp', 'cwd', 'prompt', 'transformedPrompt'):
            payload = self.__payload.copy()
            del payload[field]
            invalid.append(json.dumps(payload).encode('utf-8'))
        for field in ('sessionId', 'cwd', 'prompt', 'transformedPrompt'):
            for value in (None, 42, False, [], {}):
                invalid.append(json.dumps({**self.__payload, field: value}).encode('utf-8'))
        for changes in (
            {'sessionId': ''}, {'sessionId': ' '}, {'sessionId': '../bad'}, {'cwd': 'relative'},
            *({'timestamp': value} for value in (-1, True, 1.5, '123', None)),
        ):
            invalid.append(json.dumps({**self.__payload, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to log transformed prompt:', result.stderr)
                self.assertFalse(os.path.exists(self.__log_root))

    def test_corrupt_debug_state_reports_error(self):
        state_path = os.path.join(self.__debug_directory, f'{self.__session_id}.json')
        for raw in (b'{', b'null', b'[]', b'{"enabled":"\xff"}'):
            with self.subTest(raw=raw):
                with open(state_path, 'wb') as file:
                    file.write(raw)
                result = self.__invoke()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to log transformed prompt:', result.stderr)
                self.assertFalse(os.path.exists(self.__log_root))

    def test_log_location_conflict_reports_error(self):
        os.makedirs(os.path.dirname(self.__log_root), exist_ok=True)
        with open(self.__log_root, 'w', encoding='utf-8', newline='\n') as file:
            file.write('A file occupies the log directory')
        result = self.__invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to log transformed prompt:', result.stderr)

    def test_failed_finalization_removes_temporary_log(self):
        save_log = runpy.run_path(_hook_path)['_save_log']
        with (
            patch('tempfile.gettempdir', return_value=self.__root),
            patch('os.replace', side_effect=OSError('test finalization failure')),
        ):
            with self.assertRaisesRegex(OSError, 'test finalization failure'):
                save_log('report', self.__session_id, self.__payload['timestamp'])
        self.assertEqual(os.listdir(self.__log_directory), [])


if __name__ == '__main__':
    _run_tests()
