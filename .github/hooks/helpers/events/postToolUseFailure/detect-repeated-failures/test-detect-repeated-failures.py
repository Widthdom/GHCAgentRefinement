import json
import os
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from unittest.mock import patch


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-repeated-failures.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class DetectRepeatedFailuresTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-repeated-failures-test-')
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.session_id = str(uuid.uuid4())
        self.state_directory = os.path.join(self.root, 'ghc-agent-refinement', 'hook-states', 'repeated-failures')
        self.state_path = os.path.join(self.state_directory, f'{self.session_id}.sqlite3')
        self.payload = {
            'sessionId': self.session_id,
            'timestamp': 0,
            'cwd': self.root,
            'toolName': 'view',
            'toolArgs': {'path': '存在しない.py', 'limit': 20},
            'error': 'File not found: 存在しない.py',
        }

    def invoke(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=10,
        )

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_notification(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'additionalContext'})
        self.assertIn('three times within five minutes', output['additionalContext'])
        self.assertIn('what you will change', output['additionalContext'])
        self.assertNotIn(self.payload['error'], output['additionalContext'])

    def read_rows(self, state_path=None):
        with closing(sqlite3.connect(state_path or self.state_path)) as connection:
            return connection.execute('SELECT fingerprint, first_seen, count FROM failures').fetchall()

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'postToolUseFailure.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        self.assertEqual(configuration['version'], 1)
        self.assertEqual(set(configuration['hooks']), {'postToolUseFailure'})
        entries = configuration['hooks']['postToolUseFailure']
        self.assertEqual(len(entries), 1)
        relative_path = '.github/hooks/helpers/events/postToolUseFailure/detect-repeated-failures/detect-repeated-failures.py'
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')

    def test_notifies_only_on_third_failure(self):
        self.assert_silent(self.invoke())
        self.assert_silent(self.invoke())
        self.assert_notification(self.invoke())
        for _ in range(3):
            self.assert_silent(self.invoke())
        self.assertEqual(self.read_rows()[0][2], 3)

    def test_json_object_key_order_does_not_change_identity(self):
        payload = {**self.payload, 'toolArgs': {'options': {'a': 1, 'b': 2}, 'path': 'missing'}}
        self.assert_silent(self.invoke(payload))
        self.assert_silent(self.invoke(payload))
        payload['toolArgs'] = {'path': 'missing', 'options': {'b': 2, 'a': 1}}
        self.assert_notification(self.invoke(payload))

    def test_different_failures_do_not_share_counts(self):
        alternatives = {
            'cwd': os.path.join(self.root, 'another-directory'),
            'toolName': 'edit',
            'toolArgs': {'path': 'another.py'},
            'error': 'Permission denied',
        }
        for field, value in alternatives.items():
            with self.subTest(field=field):
                session = str(uuid.uuid4())
                original = {**self.payload, 'sessionId': session}
                different = {**original, field: value}
                self.assert_silent(self.invoke(original))
                self.assert_silent(self.invoke(different))
                self.assert_silent(self.invoke(original))
                self.assert_notification(self.invoke(original))
                self.assert_silent(self.invoke(different))

    def test_sessions_are_isolated_and_uuid_is_normalized(self):
        self.assert_silent(self.invoke())
        self.assert_silent(self.invoke())
        other = {**self.payload, 'sessionId': str(uuid.uuid4())}
        self.assert_silent(self.invoke(other))
        normalized = {**self.payload, 'sessionId': ' {' + self.session_id.upper() + '} '}
        self.assert_notification(self.invoke(normalized))
        self.assert_silent(self.invoke(other))

    def test_tool_args_can_be_any_json_value(self):
        for args in (None, 'literal arguments', ['a', 1], 42, False):
            with self.subTest(args=args):
                payload = {**self.payload, 'toolArgs': args, 'sessionId': str(uuid.uuid4())}
                self.assert_silent(self.invoke(payload))
                self.assert_silent(self.invoke(payload))
                self.assert_notification(self.invoke(payload))

    def test_subagents_do_not_create_or_consume_parent_state(self):
        child = {**self.payload, 'agentId': 'child-agent'}
        self.assert_silent(self.invoke(child))
        self.assertFalse(os.path.exists(self.state_directory))
        self.assert_silent(self.invoke())
        self.assert_silent(self.invoke())
        for _ in range(3):
            self.assert_silent(self.invoke(child))
        self.assertEqual(self.read_rows()[0][2], 2)
        self.assert_notification(self.invoke())

    def test_window_and_clock_rollback_reset_counts(self):
        module = runpy.run_path(_hook_path)
        record = module['_record_failure']
        os.makedirs(self.state_directory)
        # テスト時だけ時計を差し替え、5分ちょうどと期限超過を待たずに確認する。
        for first_seen, current, expected in ((1000, 1300, True), (2000, 2301, False), (3000, 2999, False)):
            with self.subTest(first_seen=first_seen, current=current):
                fingerprint = str(first_seen)
                with patch('time.time', return_value=first_seen):
                    self.assertFalse(record(self.state_path, fingerprint))
                    self.assertFalse(record(self.state_path, fingerprint))
                with patch('time.time', return_value=current):
                    self.assertEqual(record(self.state_path, fingerprint), expected)

    def test_no_plaintext_tool_data_is_saved(self):
        self.assert_silent(self.invoke())
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0][0], r'^[0-9a-f]{64}$')
        self.assertLessEqual(rows[0][1], time.time())
        with open(self.state_path, 'rb') as file:
            saved = file.read()
        for value in (self.root, self.payload['toolName'], self.payload['error'], '存在しない.py'):
            self.assertNotIn(value.encode('utf-8'), saved)

    def test_invalid_input_does_not_change_state(self):
        self.assert_silent(self.invoke())
        before = self.read_rows()
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'true', b'{"error":"\xff"}']
        for field in ('sessionId', 'cwd', 'toolName', 'error', 'toolArgs'):
            payload = self.payload.copy()
            del payload[field]
            invalid.append(json.dumps(payload).encode('utf-8'))
        for field in ('sessionId', 'cwd', 'toolName', 'error'):
            for value in ('', ' \t', None, 3, False, [], {}):
                invalid.append(json.dumps({**self.payload, field: value}).encode('utf-8'))
        for changes in ({'cwd': 'relative'}, {'sessionId': '../bad'}, {'toolArgs': float('nan')}):
            invalid.append(json.dumps({**self.payload, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to detect repeated tool failures:', result.stderr)
                self.assertEqual(self.read_rows(), before)

    def test_corrupt_state_reports_error_without_context(self):
        os.makedirs(self.state_directory)
        with open(self.state_path, 'wb') as file:
            file.write(b'not a SQLite database')
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to detect repeated tool failures:', result.stderr)

    def test_unwritable_state_location_reports_error(self):
        os.makedirs(os.path.dirname(self.state_directory))
        with open(self.state_directory, 'w', encoding='utf-8') as file:
            file.write('a file occupies the state directory')
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to detect repeated tool failures:', result.stderr)

    def test_concurrent_failures_count_once_and_notify_once(self):
        raw = json.dumps(self.payload).encode('utf-8')
        processes = []
        for _ in range(6):
            process = subprocess.Popen(
                [sys.executable, _hook_path], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment,
            )
            self.addCleanup(self.finish_process, process)
            processes.append(process)
        for process in processes:
            process.stdin.write(raw)
            process.stdin.close()
            process.stdin = None
        notifications = 0
        for process in processes:
            output, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, error.decode('utf-8'))
            self.assertEqual(error, b'')
            if output:
                notifications += 1
                self.assertIn('additionalContext', json.loads(output))
        self.assertEqual(notifications, 1)
        self.assertEqual(self.read_rows()[0][2], 3)

    @staticmethod
    def finish_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate()


if __name__ == '__main__':
    run_tests()
