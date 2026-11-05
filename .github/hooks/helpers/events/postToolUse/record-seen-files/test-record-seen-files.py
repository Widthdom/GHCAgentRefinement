import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'record-seen-files.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class RecordSeenFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-record-seen-files-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__session_id = str(uuid.uuid4())
        self.__state_directory = os.path.join(self.__root, 'ghc-agent-refinement', 'hook-states', 'known-files')
        self.__state_path = os.path.join(self.__state_directory, f'{self.__session_id}.sqlite3')
        self.__workspace = os.path.join(self.__root, 'workspace')
        os.makedirs(os.path.join(self.__workspace, 'sub'))
        self.__target = os.path.join(self.__workspace, '対象.py')
        self.__write(self.__target)
        self.__payload = {
            'sessionId': self.__session_id,
            'timestamp': 0,
            'cwd': self.__workspace,
            'toolName': 'view',
            'toolArgs': {'path': self.__target},
            'toolResult': {'resultType': 'success', 'textResultForLlm': '1. a'},
        }

    @staticmethod
    def __write(path, content='a\n'):
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __invoke(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __read_rows(self):
        if not os.path.exists(self.__state_path):
            return {}
        with closing(sqlite3.connect(self.__state_path)) as connection:
            return dict(connection.execute('SELECT file, state FROM files').fetchall())

    @staticmethod
    def __key(path):
        return hashlib.sha256(os.path.normcase(os.path.realpath(path)).encode('utf-8')).hexdigest()

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'postToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/postToolUse/record-seen-files/record-seen-files.py'
        entries = [
            entry for entry in configuration['hooks']['postToolUse']
            if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        for tool_name in ('view', 'functions.view', 'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'):
            with self.subTest(tool_name=tool_name):
                self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        for tool_name in ('bash', 'rg', 'glob', 'skill', 'preview'):
            with self.subTest(tool_name=tool_name):
                self.assertNotRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')

    def test_records_viewed_and_edited_files(self):
        cases = (
            ('view', {'path': self.__target, 'view_range': [1, 10]}),
            ('functions.view', {'path': '対象.py'}),
            ('edit', {'path': './sub/../対象.py', 'old_str': 'a', 'new_str': 'b'}),
            ('create', {'path': '対象.py', 'file_text': 'a'}),
            ('insert', json.dumps({'path': '対象.py', 'insert_line': 1})),
            ('str_replace_editor', {'command': 'view', 'path': '対象.py'}),
        )
        for tool_name, tool_args in cases:
            with self.subTest(tool_name=tool_name):
                payload = {**self.__payload, 'sessionId': str(uuid.uuid4()), 'toolName': tool_name, 'toolArgs': tool_args}
                self.__assert_silent(self.__invoke(payload))
                self.__state_path = os.path.join(self.__state_directory, f'{payload["sessionId"]}.sqlite3')
                self.assertEqual(self.__read_rows(), {self.__key(self.__target): 'seen'})

    def test_apply_patch_records_files_that_exist_after_patch(self):
        added = os.path.join(self.__workspace, 'sub', 'added.txt')
        moved = os.path.join(self.__workspace, 'moved.py')
        self.__write(added)
        self.__write(moved)
        patch_text = (
            '*** Begin Patch\n*** Update File: 対象.py\n@@\n-a\n+b\n'
            '*** Add File: sub/added.txt\n+x\n'
            '*** Update File: old.py\n*** Move to: moved.py\n@@\n-a\n+b\n'
            '*** Delete File: deleted.py\n*** End Patch\n'
        )
        for tool_args in ({'input': patch_text}, patch_text):
            with self.subTest(tool_args=type(tool_args).__name__):
                payload = {**self.__payload, 'sessionId': str(uuid.uuid4()), 'toolName': 'apply_patch', 'toolArgs': tool_args}
                self.__state_path = os.path.join(self.__state_directory, f'{payload["sessionId"]}.sqlite3')
                self.__assert_silent(self.__invoke(payload))
                self.assertEqual(
                    self.__read_rows(), {self.__key(path): 'seen' for path in (self.__target, added, moved)},
                )

    def test_read_overrides_denied_state(self):
        os.makedirs(self.__state_directory)
        with closing(sqlite3.connect(self.__state_path)) as connection, connection:
            connection.execute('CREATE TABLE files (file TEXT PRIMARY KEY, state TEXT NOT NULL)')
            connection.execute('INSERT INTO files VALUES (?, ?)', (self.__key(self.__target), 'denied'))
        self.__assert_silent(self.__invoke())
        self.assertEqual(self.__read_rows(), {self.__key(self.__target): 'seen'})

    def test_skips_other_tools_directories_and_missing_files(self):
        cases = (
            {'toolName': 'bash', 'toolArgs': {'command': 'cat 対象.py'}},
            {'toolName': 'rg', 'toolArgs': {'pattern': 'a', 'path': '対象.py'}},
            {'toolArgs': {'path': self.__workspace}},
            {'toolArgs': {'path': 'missing.py'}},
            {'toolArgs': {}},
            {'toolArgs': {'path': ' '}},
            {'toolArgs': None},
            {'toolName': 'apply_patch', 'toolArgs': '*** Begin Patch\n*** Delete File: 対象.py\n*** End Patch\n'},
            {'toolName': 'apply_patch', 'toolArgs': 'not a patch'},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.__assert_silent(self.__invoke({**self.__payload, **changes}))
        self.assertFalse(os.path.exists(self.__state_directory))

    def test_no_plaintext_path_is_saved(self):
        self.__assert_silent(self.__invoke())
        with open(self.__state_path, 'rb') as file:
            saved = file.read()
        for value in (self.__workspace, '対象.py'):
            self.assertNotIn(value.encode('utf-8'), saved)

    def test_invalid_input_reports_error(self):
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
                self.assertIn(b'Failed to record seen files:', result.stderr)
        self.assertFalse(os.path.exists(self.__state_directory))

    def test_corrupt_state_reports_error(self):
        os.makedirs(self.__state_directory)
        with open(self.__state_path, 'wb') as file:
            file.write(b'not a SQLite database')
        result = self.__invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to record seen files:', result.stderr)


if __name__ == '__main__':
    run_tests()
