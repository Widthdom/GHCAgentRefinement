import json
import os
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from unittest.mock import patch


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'detect-edit-oscillation.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class DetectEditOscillationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-edit-oscillation-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.session_id = str(uuid.uuid4())
        self.state_directory = os.path.join(self.root, 'ghc-agent-refinement', 'edit-oscillation')
        self.state_path = os.path.join(self.state_directory, f'{self.session_id}.sqlite3')
        self.workspace = os.path.join(self.root, 'workspace')
        os.makedirs(self.workspace)
        self.target = os.path.join(self.workspace, '対象.py')
        self.payload = {
            'sessionId': self.session_id,
            'timestamp': 0,
            'cwd': self.workspace,
            'toolName': 'edit',
            'toolArgs': {'path': '対象.py', 'old_str': 'a', 'new_str': 'b'},
            'toolResult': {'resultType': 'success', 'textResultForLlm': 'File updated.'},
        }

    def write(self, content, path=None):
        with open(path or self.target, 'w', encoding='utf-8') as file:
            file.write(content)

    def invoke(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=10,
        )

    def edit(self, content, payload=None, path=None):
        self.write(content, path)
        return self.invoke(payload)

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_notification(self, result, files):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'additionalContext'})
        self.assertIn(f'earlier within the last ten minutes, after being changed in between: {files}.', output['additionalContext'])
        self.assertIn('oscillating', output['additionalContext'])

    def read_rows(self):
        with closing(sqlite3.connect(self.state_path)) as connection:
            return connection.execute('SELECT file, content, notified FROM contents ORDER BY sequence').fetchall()

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'postToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        self.assertEqual(configuration['version'], 1)
        self.assertEqual(set(configuration['hooks']), {'postToolUse'})
        relative_path = '.github/hooks/helpers/events/postToolUse/detect-edit-oscillation/detect-edit-oscillation.py'
        # 同じイベントの他のhookが増えても、配列の位置ではなくパスで自身の設定を探す。
        entries = [entry for entry in configuration['hooks']['postToolUse'] if entry.get('bash') == f'python3 {relative_path}']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python {relative_path}')
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            with self.subTest(tool_name=tool_name):
                self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        for tool_name in ('bash', 'view', 'skill'):
            with self.subTest(tool_name=tool_name):
                self.assertNotRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')

    def test_notifies_only_once_when_content_returns(self):
        self.assert_silent(self.edit('A'))
        self.assert_silent(self.edit('B'))
        self.assert_notification(self.edit('A'), '対象.py')
        self.assert_silent(self.edit('B'))
        self.assert_silent(self.edit('A'))
        self.assert_silent(self.edit('C'))
        self.assert_silent(self.edit('B'))
        self.assert_silent(self.edit('D'))
        self.assert_notification(self.edit('C'), '対象.py')

    def test_progress_and_unchanged_writes_do_not_notify(self):
        for content in ('A', 'A', 'B', 'B', 'C', 'D'):
            self.assert_silent(self.edit(content))
        self.assertEqual(len(self.read_rows()), 4)

    def test_tool_names_and_argument_formats(self):
        cases = (
            ('create', {'path': self.target, 'file_text': 'x'}),
            ('functions.insert', {'path': './対象.py', 'insert_line': 1}),
            ('str_replace_editor', json.dumps({'command': 'str_replace', 'path': '対象.py'})),
        )
        for tool_name, tool_args in cases:
            with self.subTest(tool_name=tool_name):
                payload = {**self.payload, 'sessionId': str(uuid.uuid4()), 'toolName': tool_name, 'toolArgs': tool_args}
                self.assert_silent(self.edit('A', payload))
                self.assert_silent(self.edit('B', payload))
                self.assert_notification(self.edit('A', payload), '対象.py')

    def test_apply_patch_tracks_each_file(self):
        other = os.path.join(self.workspace, 'sub', 'other.txt')
        os.makedirs(os.path.dirname(other))
        patch_text = (
            '*** Begin Patch\n*** Update File: 対象.py\n@@\n-a\n+b\n'
            '*** Add File: sub/other.txt\n+x\n*** End Patch\n'
        )
        for tool_args in ({'input': patch_text}, patch_text):
            with self.subTest(tool_args=type(tool_args).__name__):
                payload = {**self.payload, 'sessionId': str(uuid.uuid4()), 'toolName': 'apply_patch', 'toolArgs': tool_args}
                self.write('A')
                self.assert_silent(self.edit('1', payload, other))
                self.write('B')
                self.assert_silent(self.edit('2', payload, other))
                self.write('A')
                self.assert_notification(self.edit('1', payload, other), f'{os.path.join("sub", "other.txt")}, 対象.py')

    def test_deleted_file_counts_as_content(self):
        self.assert_silent(self.edit('A'))
        os.remove(self.target)
        self.assert_silent(self.invoke())
        self.assert_notification(self.edit('A'), '対象.py')

    def test_file_outside_cwd_is_shown_as_absolute_path(self):
        outside = os.path.join(self.root, 'outside.py')
        payload = {**self.payload, 'toolArgs': {'path': outside}}
        self.assert_silent(self.edit('A', payload, outside))
        self.assert_silent(self.edit('B', payload, outside))
        self.assert_notification(self.edit('A', payload, outside), os.path.normcase(outside))

    def test_skips_non_edit_tools_and_missing_paths(self):
        cases = (
            {'toolName': 'bash', 'toolArgs': {'command': 'echo A > 対象.py'}},
            {'toolName': 'view', 'toolArgs': {'path': '対象.py'}},
            {'toolArgs': {}},
            {'toolArgs': {'path': ' '}},
            {'toolArgs': None},
            {'toolName': 'apply_patch', 'toolArgs': 'not a patch'},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                payload = {**self.payload, **changes}
                for content in ('A', 'B', 'A'):
                    self.assert_silent(self.edit(content, payload))
                self.assertFalse(os.path.exists(self.state_directory))

    def test_sessions_are_isolated_and_uuid_is_normalized(self):
        self.assert_silent(self.edit('A'))
        other = {**self.payload, 'sessionId': str(uuid.uuid4())}
        self.assert_silent(self.edit('B', other))
        self.assert_silent(self.edit('A', other))
        normalized = {**self.payload, 'sessionId': ' {' + self.session_id.upper() + '} '}
        self.assert_silent(self.edit('B', normalized))
        self.assert_notification(self.edit('A', normalized), '対象.py')

    def test_window_and_clock_rollback_reset_history(self):
        module = runpy.run_path(hook_path)
        record = module['record_edits']
        os.makedirs(self.state_directory)
        # テスト時だけ時計を差し替え、10分ちょうどと期限超過を待たずに確認する。
        for first_seen, current, expected in ((1000, 1600, ['p']), (2000, 2601, []), (3000, 2999, [])):
            with self.subTest(first_seen=first_seen, current=current):
                key = str(first_seen)
                with patch('time.time', return_value=first_seen):
                    self.assertEqual(record(self.state_path, [('p', key, 'A')]), [])
                    self.assertEqual(record(self.state_path, [('p', key, 'B')]), [])
                with patch('time.time', return_value=current):
                    self.assertEqual(record(self.state_path, [('p', key, 'A')]), expected)

    def test_no_plaintext_path_or_content_is_saved(self):
        self.assert_silent(self.edit('秘密の内容'))
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0][0], r'^[0-9a-f]{64}$')
        self.assertRegex(rows[0][1], r'^[0-9a-f]{64}$')
        with open(self.state_path, 'rb') as file:
            saved = file.read()
        for value in (self.workspace, '対象.py', '秘密の内容'):
            self.assertNotIn(value.encode('utf-8'), saved)

    def test_invalid_input_does_not_change_state(self):
        self.assert_silent(self.edit('A'))
        before = self.read_rows()
        self.write('B')
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'true', b'{"toolName":"\xff"}']
        for field in ('sessionId', 'cwd', 'toolName', 'toolArgs'):
            payload = self.payload.copy()
            del payload[field]
            invalid.append(json.dumps(payload).encode('utf-8'))
        for field in ('sessionId', 'cwd', 'toolName'):
            for value in ('', ' \t', None, 3, False, [], {}):
                invalid.append(json.dumps({**self.payload, field: value}).encode('utf-8'))
        for changes in ({'cwd': 'relative'}, {'sessionId': '../bad'}):
            invalid.append(json.dumps({**self.payload, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to detect edit oscillation:', result.stderr)
                self.assertEqual(self.read_rows(), before)

    def test_corrupt_state_reports_error_without_context(self):
        os.makedirs(self.state_directory)
        with open(self.state_path, 'wb') as file:
            file.write(b'not a SQLite database')
        result = self.edit('A')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to detect edit oscillation:', result.stderr)

    def test_unwritable_state_location_reports_error(self):
        os.makedirs(os.path.dirname(self.state_directory))
        with open(self.state_directory, 'w', encoding='utf-8') as file:
            file.write('a file occupies the state directory')
        result = self.edit('A')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to detect edit oscillation:', result.stderr)

    def test_concurrent_edits_record_once_and_notify_once(self):
        self.assert_silent(self.edit('A'))
        self.assert_silent(self.edit('B'))
        self.write('A')
        raw = json.dumps(self.payload).encode('utf-8')
        processes = []
        for _ in range(6):
            process = subprocess.Popen(
                [sys.executable, hook_path], stdin=subprocess.PIPE,
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
        self.assertEqual(len(self.read_rows()), 2)

    @staticmethod
    def finish_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate()


if __name__ == '__main__':
    run_tests()
