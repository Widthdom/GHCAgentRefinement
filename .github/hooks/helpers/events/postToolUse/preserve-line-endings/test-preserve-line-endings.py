import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'preserve-line-endings.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class PreserveLineEndingsTests(unittest.TestCase):
    def setUp(self):
        if shutil.which('git') is None:
            self.skipTest('git is not installed.')
        temporary = tempfile.TemporaryDirectory(prefix='ghc-preserve-line-endings-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        # 利用者のgit設定（core.autocrlfなど）に結果を左右されないよう、グローバル・システム設定を読まない。
        self.environment.update({
            'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root,
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
        })
        self.session_id = str(uuid.uuid4())
        self.state_directory = os.path.join(self.root, 'ghc-agent-refinement', 'hook-states', 'preserve-line-endings')
        self.state_path = os.path.join(self.state_directory, f'{self.session_id}.sqlite3')
        self.workspace = os.path.join(self.root, 'workspace')
        os.makedirs(self.workspace)
        self.git('init', '-q')
        self.target = os.path.join(self.workspace, '対象.py')
        self.payload = {
            'sessionId': self.session_id,
            'timestamp': 0,
            'cwd': self.workspace,
            'toolName': 'edit',
            'toolArgs': {'path': '対象.py', 'old_str': 'a', 'new_str': 'b'},
            'toolResult': {'resultType': 'success', 'textResultForLlm': 'File updated.'},
        }

    def git(self, *args):
        subprocess.run(
            ['git', '-c', 'user.name=test', '-c', 'user.email=test@example.com', *args],
            cwd=self.workspace, env=self.environment, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def write(self, content, path=None):
        with open(path or self.target, 'wb') as file:
            file.write(content)

    def commit(self, content, path=None):
        self.write(content, path)
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', 'commit')

    def invoke(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=20,
        )

    def edit(self, content, payload=None, path=None):
        self.write(content, path)
        return self.invoke(payload)

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_notification(self, result, changes):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'additionalContext'})
        self.assertIn(f'no longer match the file format of their committed version: {changes}.', output['additionalContext'])
        self.assertIn('restore the original format', output['additionalContext'])

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'postToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/postToolUse/preserve-line-endings/preserve-line-endings.py'
        entries = [
            entry for entry in configuration['hooks']['postToolUse']
            if entry.get('bash') == f'python3 {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python {relative_path}')
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            with self.subTest(tool_name=tool_name):
                self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        for tool_name in ('bash', 'view', 'skill'):
            with self.subTest(tool_name=tool_name):
                self.assertNotRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')

    def test_detects_each_format_change(self):
        cases = (
            (b'a\r\nb\r\n', b'a\nb\nc\n', 'line endings changed from CRLF to LF'),
            (b'a\nb\n', b'a\r\nb\r\n', 'line endings changed from LF to CRLF'),
            (b'a\nb\n', b'a\nb\r\nc\n', 'line endings changed from LF to mixed CRLF/LF'),
            (b'\xef\xbb\xbfa\n', b'a\n', 'UTF-8 BOM removed'),
            (b'a\n', b'\xef\xbb\xbfa\n', 'UTF-8 BOM added'),
            (b'a\nb\n', b'a\nb', 'final newline removed'),
            (b'a\nb', b'a\nb\n', 'final newline added'),
            (b'\xef\xbb\xbfa\r\nb\r\n', b'a\nb', 'line endings changed from CRLF to LF, UTF-8 BOM removed, final newline removed'),
        )
        for committed, edited, changes in cases:
            with self.subTest(changes=changes):
                self.commit(committed)
                payload = {**self.payload, 'sessionId': str(uuid.uuid4())}
                self.assert_notification(self.edit(edited, payload), f'対象.py ({changes})')

    def test_preserved_format_is_silent_and_creates_no_state(self):
        cases = (
            (b'a\r\nb\r\n', b'a\r\nchanged\r\nc\r\n'),
            (b'\xef\xbb\xbfa\n', b'\xef\xbb\xbfchanged\n'),
            (b'a\nb', b'a\nchanged'),
            # 元から混在しているファイル、改行のない内容、空ファイルは比較しない。
            (b'a\r\nb\n', b'a\nb\n'),
            (b'no newline', b'a\r\nb'),
            (b'', b'a'),
            (b'a\n', b''),
            # NUL文字を含む内容はバイナリやUTF-16とみなす。
            (b'a\nb\n', b'a\r\n\0b\r\n'),
            ('a\nb\n'.encode('utf-16'), 'a\r\nb\r\n'.encode('utf-16')),
        )
        for committed, edited in cases:
            with self.subTest(committed=committed, edited=edited):
                self.commit(committed)
                self.assert_silent(self.edit(edited))
        self.assertFalse(os.path.exists(self.state_directory))

    def test_autocrlf_checkout_is_the_baseline(self):
        # autocrlfではHEADのLFがチェックアウト時にCRLFへ変換されるため、CRLFの作業ツリーを基準とする。
        self.git('config', 'core.autocrlf', 'true')
        self.commit(b'a\r\nb\r\n')
        self.assert_silent(self.edit(b'a\r\nchanged\r\n'))
        self.assert_notification(self.edit(b'a\nchanged\n'), '対象.py (line endings changed from CRLF to LF)')

    def test_gitattributes_eol_is_the_baseline(self):
        self.commit(b'*.py text eol=crlf\n', os.path.join(self.workspace, '.gitattributes'))
        self.commit(b'a\nb\n')
        self.assert_silent(self.edit(b'a\r\nchanged\r\n'))
        self.assert_notification(self.edit(b'a\nchanged\n'), '対象.py (line endings changed from CRLF to LF)')

    def test_notifies_once_until_format_is_restored(self):
        self.commit(b'a\r\nb\r\n')
        self.assert_notification(self.edit(b'a\nb\n'), '対象.py (line endings changed from CRLF to LF)')
        self.assert_silent(self.edit(b'a\nchanged\n'))
        # 別の種類の変化が加わった場合は、ファイルの変化をまとめて改めて通知する。
        self.assert_notification(
            self.edit(b'a\nchanged'), '対象.py (line endings changed from CRLF to LF, final newline removed)',
        )
        self.assert_silent(self.edit(b'a\nchanged'))
        # 元の形式に戻った後で再び崩れた場合も、改めて通知する。
        self.assert_silent(self.edit(b'a\r\nchanged\r\n'))
        self.assert_notification(self.edit(b'a\nchanged\n'), '対象.py (line endings changed from CRLF to LF)')

    def test_sessions_are_isolated_and_uuid_is_normalized(self):
        self.commit(b'a\r\nb\r\n')
        self.assert_notification(self.edit(b'a\nb\n'), '対象.py (line endings changed from CRLF to LF)')
        other = {**self.payload, 'sessionId': str(uuid.uuid4())}
        self.assert_notification(self.invoke(other), '対象.py (line endings changed from CRLF to LF)')
        normalized = {**self.payload, 'sessionId': ' {' + self.session_id.upper() + '} '}
        self.assert_silent(self.invoke(normalized))

    def test_tool_names_and_argument_formats(self):
        self.commit(b'a\r\nb\r\n')
        cases = (
            ('create', {'path': self.target, 'file_text': 'x'}),
            ('functions.insert', {'path': './対象.py', 'insert_line': 1}),
            ('str_replace_editor', json.dumps({'command': 'str_replace', 'path': '対象.py'})),
        )
        for tool_name, tool_args in cases:
            with self.subTest(tool_name=tool_name):
                payload = {**self.payload, 'sessionId': str(uuid.uuid4()), 'toolName': tool_name, 'toolArgs': tool_args}
                self.assert_notification(self.edit(b'a\nb\n', payload), '対象.py (line endings changed from CRLF to LF)')

    def test_apply_patch_checks_each_existing_file(self):
        other = os.path.join(self.workspace, 'sub', 'other.txt')
        os.makedirs(os.path.dirname(other))
        self.write(b'x\n', other)
        self.commit(b'a\r\nb\r\n')
        patch_text = (
            '*** Begin Patch\n*** Update File: 対象.py\n@@\n-a\n+b\n'
            '*** Update File: sub/other.txt\n@@\n-x\n+y\n'
            '*** Delete File: deleted.txt\n*** Add File: added.txt\n+z\n*** End Patch\n'
        )
        self.write(b'z', os.path.join(self.workspace, 'added.txt'))
        for tool_args in ({'input': patch_text}, patch_text):
            with self.subTest(tool_args=type(tool_args).__name__):
                payload = {**self.payload, 'sessionId': str(uuid.uuid4()), 'toolName': 'apply_patch', 'toolArgs': tool_args}
                self.write(b'a\nb\n')
                self.assert_notification(
                    self.edit(b'y', payload, other),
                    f'{os.path.join("sub", "other.txt")} (final newline removed); 対象.py (line endings changed from CRLF to LF)',
                )

    def test_subdirectory_cwd_and_file_outside_cwd(self):
        nested = os.path.join(self.workspace, 'sub')
        os.makedirs(nested)
        self.commit(b'a\r\nb\r\n')
        payload = {**self.payload, 'cwd': nested, 'toolArgs': {'path': os.path.join('..', '対象.py')}}
        self.assert_notification(self.edit(b'a\nb\n', payload), f'{os.path.normcase(self.target)} (line endings changed from CRLF to LF)')

    def test_skips_files_without_committed_version(self):
        self.commit(b'a\r\nb\r\n')
        outside = os.path.join(self.root, 'outside.py')
        untracked = os.path.join(self.workspace, 'untracked.py')
        self.write(b'a\r\nb\r\n', outside)
        cases = (
            ({'toolArgs': {'path': 'new.py'}}, os.path.join(self.workspace, 'new.py')),
            ({'toolArgs': {'path': 'untracked.py'}}, untracked),
            ({'toolArgs': {'path': outside}}, outside),
            ({'toolArgs': {'path': 'missing.py'}}, None),
            ({'toolName': 'bash', 'toolArgs': {'command': 'echo a > 対象.py'}}, self.target),
            ({'toolName': 'view', 'toolArgs': {'path': '対象.py'}}, self.target),
            ({'toolArgs': {}}, None),
            ({'toolArgs': {'path': ' '}}, None),
            ({'toolArgs': None}, None),
            ({'toolName': 'apply_patch', 'toolArgs': 'not a patch'}, None),
        )
        for changes, path in cases:
            with self.subTest(changes=changes):
                if path:
                    self.write(b'a\nb\n', path)
                self.assert_silent(self.invoke({**self.payload, **changes}))
                self.write(b'a\r\nb\r\n')
        self.assertFalse(os.path.exists(self.state_directory))

    def test_skips_without_git(self):
        self.commit(b'a\r\nb\r\n')
        empty_bin = os.path.join(self.root, 'empty-bin')
        os.makedirs(empty_bin)
        self.environment['PATH'] = empty_bin
        self.assert_silent(self.edit(b'a\nb\n'))

    def test_invalid_input_reports_error(self):
        self.commit(b'a\r\nb\r\n')
        self.write(b'a\nb\n')
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
                self.assertIn(b'Failed to check line endings:', result.stderr)
        self.assertFalse(os.path.exists(self.state_directory))

    def test_corrupt_state_reports_error_without_context(self):
        self.commit(b'a\r\nb\r\n')
        os.makedirs(self.state_directory)
        with open(self.state_path, 'wb') as file:
            file.write(b'not a SQLite database')
        result = self.edit(b'a\nb\n')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to check line endings:', result.stderr)

    def test_no_plaintext_path_or_content_is_saved(self):
        self.commit('秘密の内容\r\n'.encode('utf-8'))
        self.assert_notification(self.edit('秘密の内容\n'.encode('utf-8')), '対象.py (line endings changed from CRLF to LF)')
        with closing(sqlite3.connect(self.state_path)) as connection:
            rows = connection.execute('SELECT file, change FROM notifications').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0][0], r'^[0-9a-f]{64}$')
        with open(self.state_path, 'rb') as file:
            saved = file.read()
        for value in (self.workspace, '対象.py', '秘密の内容'):
            self.assertNotIn(value.encode('utf-8'), saved)

    def test_concurrent_edits_notify_once(self):
        self.commit(b'a\r\nb\r\n')
        self.write(b'a\nb\n')
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
            output, error = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, error.decode('utf-8'))
            self.assertEqual(error, b'')
            if output:
                notifications += 1
                self.assertIn('additionalContext', json.loads(output))
        self.assertEqual(notifications, 1)

    @staticmethod
    def finish_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate()


if __name__ == '__main__':
    run_tests()
