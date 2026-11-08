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
        self.__root = temporary.name
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__session_id = str(uuid.uuid4())
        self.__state_directory = os.path.join(self.__root, 'ghc-agent-refinement', 'hook-states', 'repeated-failures')
        self.__state_path = os.path.join(self.__state_directory, f'{self.__session_id}.sqlite3')
        self.__payload = {
            'sessionId': self.__session_id,
            'timestamp': 0,
            'cwd': self.__root,
            'toolName': 'view',
            'toolArgs': {'path': '存在しない.py', 'limit': 20},
            'error': 'File not found: 存在しない.py',
        }

    def __invoke(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_notification(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'additionalContext'})
        self.assertIn('three times within five minutes', output['additionalContext'])
        self.assertIn('what you will change', output['additionalContext'])
        self.assertNotIn(self.__payload['error'], output['additionalContext'])

    def __read_rows(self, state_path=None):
        with closing(sqlite3.connect(state_path or self.__state_path)) as connection:
            return connection.execute('SELECT fingerprint, first_seen, count FROM failures').fetchall()

    def test_notifies_only_on_third_failure(self):
        self.__assert_silent(self.__invoke())
        self.__assert_silent(self.__invoke())
        self.__assert_notification(self.__invoke())
        for _ in range(3):
            self.__assert_silent(self.__invoke())
        self.assertEqual(self.__read_rows()[0][2], 3)

    def test_json_object_key_order_does_not_change_identity(self):
        payload = {**self.__payload, 'toolArgs': {'options': {'a': 1, 'b': 2}, 'path': 'missing'}}
        self.__assert_silent(self.__invoke(payload))
        self.__assert_silent(self.__invoke(payload))
        payload['toolArgs'] = {'path': 'missing', 'options': {'b': 2, 'a': 1}}
        self.__assert_notification(self.__invoke(payload))

    def test_different_failures_do_not_share_counts(self):
        alternatives = {
            'cwd': os.path.join(self.__root, 'another-directory'),
            'toolName': 'edit',
            'toolArgs': {'path': 'another.py'},
            'error': 'Permission denied',
        }
        for field, value in alternatives.items():
            with self.subTest(field=field):
                session = str(uuid.uuid4())
                original = {**self.__payload, 'sessionId': session}
                different = {**original, field: value}
                self.__assert_silent(self.__invoke(original))
                self.__assert_silent(self.__invoke(different))
                self.__assert_silent(self.__invoke(original))
                self.__assert_notification(self.__invoke(original))
                self.__assert_silent(self.__invoke(different))

    def test_sessions_are_isolated_and_uuid_is_normalized(self):
        self.__assert_silent(self.__invoke())
        self.__assert_silent(self.__invoke())
        other = {**self.__payload, 'sessionId': str(uuid.uuid4())}
        self.__assert_silent(self.__invoke(other))
        normalized = {**self.__payload, 'sessionId': ' {' + self.__session_id.upper() + '} '}
        self.__assert_notification(self.__invoke(normalized))
        self.__assert_silent(self.__invoke(other))

    def test_tool_args_can_be_any_json_value(self):
        for args in (None, 'literal arguments', ['a', 1], 42, False):
            with self.subTest(args=args):
                payload = {**self.__payload, 'toolArgs': args, 'sessionId': str(uuid.uuid4())}
                self.__assert_silent(self.__invoke(payload))
                self.__assert_silent(self.__invoke(payload))
                self.__assert_notification(self.__invoke(payload))

    def test_subagents_do_not_create_or_consume_parent_state(self):
        child = {**self.__payload, 'agentId': 'child-agent'}
        self.__assert_silent(self.__invoke(child))
        self.assertFalse(os.path.exists(self.__state_directory))
        self.__assert_silent(self.__invoke())
        self.__assert_silent(self.__invoke())
        for _ in range(3):
            self.__assert_silent(self.__invoke(child))
        self.assertEqual(self.__read_rows()[0][2], 2)
        self.__assert_notification(self.__invoke())

    def test_window_and_clock_rollback_reset_counts(self):
        module = runpy.run_path(_hook_path)
        record = module['_record_failure']
        os.makedirs(self.__state_directory)
        # テスト時だけ時計を差し替え、5分ちょうどと期限超過を待たずに確認する。
        for first_seen, current, expected in ((1000, 1300, True), (2000, 2301, False), (3000, 2999, False)):
            with self.subTest(first_seen=first_seen, current=current):
                fingerprint = str(first_seen)
                with patch('time.time', return_value=first_seen):
                    self.assertFalse(record(self.__state_path, fingerprint))
                    self.assertFalse(record(self.__state_path, fingerprint))
                with patch('time.time', return_value=current):
                    self.assertEqual(record(self.__state_path, fingerprint), expected)

    def test_no_plaintext_tool_data_is_saved(self):
        self.__assert_silent(self.__invoke())
        rows = self.__read_rows()
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0][0], r'^[0-9a-f]{64}$')
        self.assertLessEqual(rows[0][1], time.time())
        with open(self.__state_path, 'rb') as file:
            saved = file.read()
        for value in (self.__root, self.__payload['toolName'], self.__payload['error'], '存在しない.py'):
            self.assertNotIn(value.encode('utf-8'), saved)

    def test_invalid_input_does_not_change_state(self):
        self.__assert_silent(self.__invoke())
        before = self.__read_rows()
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'true', b'{"error":"\xff"}']
        for field in ('sessionId', 'cwd', 'toolName', 'error', 'toolArgs'):
            payload = self.__payload.copy()
            del payload[field]
            invalid.append(json.dumps(payload).encode('utf-8'))
        for field in ('sessionId', 'cwd', 'toolName', 'error'):
            for value in ('', ' \t', None, 3, False, [], {}):
                invalid.append(json.dumps({**self.__payload, field: value}).encode('utf-8'))
        for changes in ({'cwd': 'relative'}, {'sessionId': '../bad'}, {'toolArgs': float('nan')}):
            invalid.append(json.dumps({**self.__payload, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to detect repeated tool failures:', result.stderr)
                self.assertEqual(self.__read_rows(), before)

    def test_corrupt_state_reports_error_without_context(self):
        os.makedirs(self.__state_directory)
        with open(self.__state_path, 'wb') as file:
            file.write(b'not a SQLite database')
        result = self.__invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to detect repeated tool failures:', result.stderr)

    def test_unwritable_state_location_reports_error(self):
        os.makedirs(os.path.dirname(self.__state_directory))
        with open(self.__state_directory, 'w', encoding='utf-8', newline='\n') as file:
            file.write('a file occupies the state directory')
        result = self.__invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to detect repeated tool failures:', result.stderr)

    def test_concurrent_failures_count_once_and_notify_once(self):
        raw = json.dumps(self.__payload).encode('utf-8')
        processes = []
        for _ in range(6):
            process = subprocess.Popen(
                [sys.executable, _hook_path], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment,
            )
            self.addCleanup(self.__finish_process, process)
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
        self.assertEqual(self.__read_rows()[0][2], 3)

    @staticmethod
    def __finish_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate()


if __name__ == '__main__':
    run_tests()
