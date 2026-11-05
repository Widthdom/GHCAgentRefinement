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


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-edit-oscillation.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class DetectEditOscillationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-edit-oscillation-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__session_id = str(uuid.uuid4())
        self.__state_directory = os.path.join(self.__root, 'ghc-agent-refinement', 'hook-states', 'edit-oscillation')
        self.__state_path = os.path.join(self.__state_directory, f'{self.__session_id}.sqlite3')
        self.__workspace = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__workspace)
        self.__target = os.path.join(self.__workspace, '対象.py')
        self.__payload = {
            'sessionId': self.__session_id,
            'timestamp': 0,
            'cwd': self.__workspace,
            'toolName': 'edit',
            'toolArgs': {'path': '対象.py', 'old_str': 'a', 'new_str': 'b'},
            'toolResult': {'resultType': 'success', 'textResultForLlm': 'File updated.'},
        }

    def __write(self, content, path=None):
        with open(path or self.__target, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __invoke(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __edit(self, content, payload=None, path=None):
        self.__write(content, path)
        return self.__invoke(payload)

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_notification(self, result, files):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'additionalContext'})
        self.assertIn(f'earlier within the last ten minutes, after being changed in between: {files}.', output['additionalContext'])
        self.assertIn('oscillating', output['additionalContext'])

    def __read_rows(self):
        with closing(sqlite3.connect(self.__state_path)) as connection:
            return connection.execute('SELECT file, content, notified FROM contents ORDER BY sequence').fetchall()

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'postToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        self.assertEqual(configuration['version'], 1)
        self.assertEqual(set(configuration['hooks']), {'postToolUse'})
        relative_path = '.github/hooks/helpers/events/postToolUse/detect-edit-oscillation/detect-edit-oscillation.py'
        # 同じイベントの他のhookが増えても、配列の位置ではなくパスで自身の設定を探す。
        entries = [entry for entry in configuration['hooks']['postToolUse'] if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            with self.subTest(tool_name=tool_name):
                self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        for tool_name in ('bash', 'view', 'skill'):
            with self.subTest(tool_name=tool_name):
                self.assertNotRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')

    def test_notifies_only_once_when_content_returns(self):
        self.__assert_silent(self.__edit('A'))
        self.__assert_silent(self.__edit('B'))
        self.__assert_notification(self.__edit('A'), '対象.py')
        self.__assert_silent(self.__edit('B'))
        self.__assert_silent(self.__edit('A'))
        self.__assert_silent(self.__edit('C'))
        self.__assert_silent(self.__edit('B'))
        self.__assert_silent(self.__edit('D'))
        self.__assert_notification(self.__edit('C'), '対象.py')

    def test_progress_and_unchanged_writes_do_not_notify(self):
        for content in ('A', 'A', 'B', 'B', 'C', 'D'):
            self.__assert_silent(self.__edit(content))
        self.assertEqual(len(self.__read_rows()), 4)

    def test_tool_names_and_argument_formats(self):
        cases = (
            ('create', {'path': self.__target, 'file_text': 'x'}),
            ('functions.insert', {'path': './対象.py', 'insert_line': 1}),
            ('str_replace_editor', json.dumps({'command': 'str_replace', 'path': '対象.py'})),
        )
        for tool_name, tool_args in cases:
            with self.subTest(tool_name=tool_name):
                payload = {**self.__payload, 'sessionId': str(uuid.uuid4()), 'toolName': tool_name, 'toolArgs': tool_args}
                self.__assert_silent(self.__edit('A', payload))
                self.__assert_silent(self.__edit('B', payload))
                self.__assert_notification(self.__edit('A', payload), '対象.py')

    def test_apply_patch_tracks_each_file(self):
        other = os.path.join(self.__workspace, 'sub', 'other.txt')
        os.makedirs(os.path.dirname(other))
        patch_text = (
            '*** Begin Patch\n*** Update File: 対象.py\n@@\n-a\n+b\n'
            '*** Add File: sub/other.txt\n+x\n*** End Patch\n'
        )
        for tool_args in ({'input': patch_text}, patch_text):
            with self.subTest(tool_args=type(tool_args).__name__):
                payload = {**self.__payload, 'sessionId': str(uuid.uuid4()), 'toolName': 'apply_patch', 'toolArgs': tool_args}
                self.__write('A')
                self.__assert_silent(self.__edit('1', payload, other))
                self.__write('B')
                self.__assert_silent(self.__edit('2', payload, other))
                self.__write('A')
                self.__assert_notification(self.__edit('1', payload, other), 'sub/other.txt, 対象.py')

    def test_deleted_file_counts_as_content(self):
        self.__assert_silent(self.__edit('A'))
        os.remove(self.__target)
        self.__assert_silent(self.__invoke())
        self.__assert_notification(self.__edit('A'), '対象.py')

    def test_file_outside_cwd_is_shown_as_absolute_path(self):
        outside = os.path.join(self.__root, 'outside.py')
        payload = {**self.__payload, 'toolArgs': {'path': outside}}
        self.__assert_silent(self.__edit('A', payload, outside))
        self.__assert_silent(self.__edit('B', payload, outside))
        self.__assert_notification(self.__edit('A', payload, outside), os.path.normcase(outside))

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
                payload = {**self.__payload, **changes}
                for content in ('A', 'B', 'A'):
                    self.__assert_silent(self.__edit(content, payload))
                self.assertFalse(os.path.exists(self.__state_directory))

    def test_sessions_are_isolated_and_uuid_is_normalized(self):
        self.__assert_silent(self.__edit('A'))
        other = {**self.__payload, 'sessionId': str(uuid.uuid4())}
        self.__assert_silent(self.__edit('B', other))
        self.__assert_silent(self.__edit('A', other))
        normalized = {**self.__payload, 'sessionId': ' {' + self.__session_id.upper() + '} '}
        self.__assert_silent(self.__edit('B', normalized))
        self.__assert_notification(self.__edit('A', normalized), '対象.py')

    def test_window_and_clock_rollback_reset_history(self):
        module = runpy.run_path(_hook_path)
        record = module['_record_edits']
        os.makedirs(self.__state_directory)
        # テスト時だけ時計を差し替え、10分ちょうどと期限超過を待たずに確認する。
        for first_seen, current, expected in ((1000, 1600, ['p']), (2000, 2601, []), (3000, 2999, [])):
            with self.subTest(first_seen=first_seen, current=current):
                key = str(first_seen)
                with patch('time.time', return_value=first_seen):
                    self.assertEqual(record(self.__state_path, [('p', key, 'A')]), [])
                    self.assertEqual(record(self.__state_path, [('p', key, 'B')]), [])
                with patch('time.time', return_value=current):
                    self.assertEqual(record(self.__state_path, [('p', key, 'A')]), expected)

    def test_no_plaintext_path_or_content_is_saved(self):
        self.__assert_silent(self.__edit('秘密の内容'))
        rows = self.__read_rows()
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0][0], r'^[0-9a-f]{64}$')
        self.assertRegex(rows[0][1], r'^[0-9a-f]{64}$')
        with open(self.__state_path, 'rb') as file:
            saved = file.read()
        for value in (self.__workspace, '対象.py', '秘密の内容'):
            self.assertNotIn(value.encode('utf-8'), saved)

    def test_invalid_input_does_not_change_state(self):
        self.__assert_silent(self.__edit('A'))
        before = self.__read_rows()
        self.__write('B')
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'true', b'{"toolName":"\xff"}']
        for field in ('sessionId', 'cwd', 'toolName', 'toolArgs'):
            payload = self.__payload.copy()
            del payload[field]
            invalid.append(json.dumps(payload).encode('utf-8'))
        for field in ('sessionId', 'cwd', 'toolName'):
            for value in ('', ' \t', None, 3, False, [], {}):
                invalid.append(json.dumps({**self.__payload, field: value}).encode('utf-8'))
        for changes in ({'cwd': 'relative'}, {'sessionId': '../bad'}):
            invalid.append(json.dumps({**self.__payload, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to detect edit oscillation:', result.stderr)
                self.assertEqual(self.__read_rows(), before)

    def test_corrupt_state_reports_error_without_context(self):
        os.makedirs(self.__state_directory)
        with open(self.__state_path, 'wb') as file:
            file.write(b'not a SQLite database')
        result = self.__edit('A')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to detect edit oscillation:', result.stderr)

    def test_unwritable_state_location_reports_error(self):
        os.makedirs(os.path.dirname(self.__state_directory))
        with open(self.__state_directory, 'w', encoding='utf-8', newline='\n') as file:
            file.write('a file occupies the state directory')
        result = self.__edit('A')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to detect edit oscillation:', result.stderr)

    def test_concurrent_edits_record_once_and_notify_once(self):
        self.__assert_silent(self.__edit('A'))
        self.__assert_silent(self.__edit('B'))
        self.__write('A')
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
        self.assertEqual(len(self.__read_rows()), 2)

    @staticmethod
    def __finish_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate()


if __name__ == '__main__':
    run_tests()
