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
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class DebugLogTransformedPromptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc prompt-log 日本語 ')
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.session_id = str(uuid.uuid4())
        self.debug_directory = os.path.join(self.root, 'ghc-agent-refinement', 'hook-states', 'debug-mode')
        self.log_root = os.path.join(self.root, 'ghc-agent-refinement', 'debug-logs', 'prompt-transformations')
        self.log_directory = os.path.join(self.log_root, self.session_id)
        self.known_paths = set()
        self.payload = {
            'sessionId': self.session_id,
            'timestamp': 1793000000000,
            'cwd': self.root,
            'prompt': '?? 調べて\n',
            'transformedPrompt': '<current_datetime>2026-10-27</current_datetime>\n?? 調べて\n',
        }
        self.write_debug_state({'enabled': True})

    def write_debug_state(self, state, session_id=None):
        os.makedirs(self.debug_directory, exist_ok=True)
        path = os.path.join(self.debug_directory, f'{session_id or self.session_id}.json')
        with open(path, 'w', encoding='utf-8', newline='') as file:
            json.dump(state, file)
        return path

    def invoke(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.environment, timeout=10,
        )

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def logged_path(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(json.loads(result.stdout.decode('utf-8')), {})
        # 保存時にはprogressを表示しない。各呼び出しが新しく作ったログを確認する。
        paths = set(glob.glob(os.path.join(glob.escape(self.log_root), '*', '*.md')))
        created = paths - self.known_paths
        self.assertEqual(len(created), 1)
        self.known_paths = paths
        path = created.pop()
        self.assertTrue(os.path.isabs(path))
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(path.endswith('.md'))
        return path

    def read_log(self, path):
        with open(path, 'r', encoding='utf-8', newline='') as file:
            return file.read()

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'userPromptTransformed.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        self.assertEqual(configuration['version'], 1)
        self.assertEqual(set(configuration['hooks']), {'userPromptTransformed'})
        entries = configuration['hooks']['userPromptTransformed']
        relative_path = '.github/hooks/helpers/events/userPromptTransformed/debug-log-transformed-prompt/debug-log-transformed-prompt.py'
        self.assertEqual(entries, [{
            'type': 'command', 'bash': f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}', 'powershell': f'python .github/hooks/helpers/runner/run-hook.py {relative_path}',
        }])

    def test_records_prompts_and_added_runtime_context(self):
        path = self.logged_path(self.invoke())
        self.assertEqual(os.path.dirname(path), self.log_directory)
        report = self.read_log(path)
        self.assertLess(report.index('## Difference'), report.index('## User prompt'))
        self.assertIn('## User prompt (`prompt`)\n\n```text\n?? 調べて\n```', report)
        self.assertIn(self.payload['transformedPrompt'], report)
        self.assertIn('--- prompt\n+++ transformedPrompt\n', report)
        self.assertIn('+<current_datetime>2026-10-27</current_datetime>\n', report)
        self.assertIn(' ?? 調べて\n', report)
        event_json = report.split('```json\n', 1)[1].split('\n```', 1)[0]
        self.assertEqual(json.loads(event_json), {
            'sessionId': self.session_id, 'timestamp': self.payload['timestamp'], 'cwd': self.root,
        })
        with open(path, 'rb') as file:
            self.assertFalse(file.read().startswith(b'\xef\xbb\xbf'))

    def test_records_unchanged_prompt(self):
        payload = {**self.payload, 'transformedPrompt': self.payload['prompt']}
        report = self.read_log(self.logged_path(self.invoke(payload)))
        self.assertIn('No differences.', report)
        self.assertEqual(report.count('```text\n?? 調べて\n```'), 2)

    def test_missing_or_disabled_debug_mode_does_not_create_logs(self):
        self.assert_silent(self.invoke({**self.payload, 'sessionId': str(uuid.uuid4())}))
        for state in ({}, {'enabled': False}, {'enabled': None}, {'enabled': 1}, {'enabled': 'true'}):
            with self.subTest(state=state):
                self.write_debug_state(state)
                self.assert_silent(self.invoke())
                self.assertFalse(os.path.exists(self.log_root))

    def test_existing_debug_switch_controls_logging(self):
        switch_path = os.path.join(
            _hooks_directory, 'helpers', 'events', 'userPromptSubmitted', 'set-debug-mode', 'set-debug-mode.py',
        )
        for prompt, expected_enabled in (('?? 最初の依頼', True), ('次の通常の依頼', False)):
            with self.subTest(prompt=prompt):
                switch = subprocess.run(
                    [sys.executable, switch_path],
                    input=json.dumps({'sessionId': self.session_id, 'prompt': prompt}).encode('utf-8'),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=10,
                )
                self.assert_silent(switch)
                result = self.invoke({**self.payload, 'prompt': prompt, 'transformedPrompt': prompt})
                if expected_enabled:
                    self.logged_path(result)
                else:
                    self.assert_silent(result)
        self.assertEqual(len(glob.glob(os.path.join(self.log_directory, '*.md'))), 1)

    def test_sessions_are_isolated_and_uuid_is_normalized(self):
        other_session_id = str(uuid.uuid4())
        other = {**self.payload, 'sessionId': other_session_id}
        self.assert_silent(self.invoke(other))
        padded = {**self.payload, 'sessionId': ' {' + self.session_id.upper() + '} '}
        first_path = self.logged_path(self.invoke(padded))
        self.assertEqual(os.path.dirname(first_path), self.log_directory)
        self.write_debug_state({'enabled': True}, other_session_id)
        other_path = self.logged_path(self.invoke(other))
        self.assertEqual(os.path.dirname(other_path), os.path.join(self.log_root, other_session_id))

    def test_subagents_do_not_create_logs(self):
        self.assert_silent(self.invoke({**self.payload, 'agentId': 'child-agent'}))
        self.assertFalse(os.path.exists(self.log_root))

    def test_same_timestamp_keeps_separate_logs(self):
        first_path = self.logged_path(self.invoke())
        before = self.read_log(first_path)
        second = {**self.payload, 'prompt': '?? 次の依頼', 'transformedPrompt': '次の変換後の文章'}
        second_path = self.logged_path(self.invoke(second))
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(self.read_log(first_path), before)
        self.assertIn(second['transformedPrompt'], self.read_log(second_path))
        self.assertFalse(glob.glob(os.path.join(self.log_directory, '*.tmp')))

    def test_markdown_fences_in_prompt_are_preserved(self):
        prompt = '??\n`````\n# This is prompt content\n`````\n'
        payload = {**self.payload, 'prompt': prompt, 'transformedPrompt': 'Added context\n' + prompt}
        report = self.read_log(self.logged_path(self.invoke(payload)))
        self.assertIn('``````text\n' + prompt + '``````\n', report)
        self.assertIn('``````diff\n', report)

    def test_diff_marks_missing_final_newlines(self):
        payload = {**self.payload, 'prompt': '?? old', 'transformedPrompt': '?? new'}
        report = self.read_log(self.logged_path(self.invoke(payload)))
        self.assertIn('-?? old\n\\ No newline at end of file\n', report)
        self.assertIn('+?? new\n\\ No newline at end of file\n', report)

    def test_empty_strings_and_original_line_endings_are_recorded(self):
        for prompt, transformed in (('', ''), ('?? 一行目\r\n二行目\r\n', '?? 一行目\n二行目\n'), (' \t', ' \t')):
            with self.subTest(prompt=prompt, transformed=transformed):
                payload = {**self.payload, 'prompt': prompt, 'transformedPrompt': transformed}
                report = self.read_log(self.logged_path(self.invoke(payload)))
                self.assertIn('```text\n' + prompt, report)
                self.assertIn('```text\n' + transformed, report)
                if prompt != transformed:
                    self.assertIn('-?? 一行目\r\n', report)
                    self.assertIn('+?? 一行目\n', report)

    def test_invalid_payload_never_creates_logs_or_progress(self):
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'true', b'42', b'{"prompt":"\xff"}']
        for field in ('sessionId', 'timestamp', 'cwd', 'prompt', 'transformedPrompt'):
            payload = self.payload.copy()
            del payload[field]
            invalid.append(json.dumps(payload).encode('utf-8'))
        for field in ('sessionId', 'cwd', 'prompt', 'transformedPrompt'):
            for value in (None, 42, False, [], {}):
                invalid.append(json.dumps({**self.payload, field: value}).encode('utf-8'))
        for changes in (
            {'sessionId': ''}, {'sessionId': ' '}, {'sessionId': '../bad'}, {'cwd': 'relative'},
            *({'timestamp': value} for value in (-1, True, 1.5, '123', None)),
        ):
            invalid.append(json.dumps({**self.payload, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to log transformed prompt:', result.stderr)
                self.assertFalse(os.path.exists(self.log_root))

    def test_corrupt_debug_state_reports_error(self):
        state_path = os.path.join(self.debug_directory, f'{self.session_id}.json')
        for raw in (b'{', b'null', b'[]', b'{"enabled":"\xff"}'):
            with self.subTest(raw=raw):
                with open(state_path, 'wb') as file:
                    file.write(raw)
                result = self.invoke()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to log transformed prompt:', result.stderr)
                self.assertFalse(os.path.exists(self.log_root))

    def test_log_location_conflict_reports_error(self):
        os.makedirs(os.path.dirname(self.log_root), exist_ok=True)
        with open(self.log_root, 'w', encoding='utf-8') as file:
            file.write('A file occupies the log directory')
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to log transformed prompt:', result.stderr)

    def test_failed_finalization_removes_temporary_log(self):
        save_log = runpy.run_path(_hook_path)['_save_log']
        with (
            patch('tempfile.gettempdir', return_value=self.root),
            patch('os.replace', side_effect=OSError('test finalization failure')),
        ):
            with self.assertRaisesRegex(OSError, 'test finalization failure'):
                save_log('report', self.session_id, self.payload['timestamp'])
        self.assertEqual(os.listdir(self.log_directory), [])


if __name__ == '__main__':
    run_tests()
