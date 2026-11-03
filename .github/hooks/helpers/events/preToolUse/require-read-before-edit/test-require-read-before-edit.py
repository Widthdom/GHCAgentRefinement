import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'require-read-before-edit.py')
_events_directory = os.path.dirname(os.path.dirname(_script_directory))
_record_hook_path = os.path.join(_events_directory, 'postToolUse', 'record-seen-files', 'record-seen-files.py')
_hooks_directory = os.path.dirname(os.path.dirname(_events_directory))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class RequireReadBeforeEditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-require-read-before-edit-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.session_id = str(uuid.uuid4())
        self.state_directory = os.path.join(self.root, 'ghc-agent-refinement', 'hook-states', 'known-files')
        self.state_path = os.path.join(self.state_directory, f'{self.session_id}.sqlite3')
        self.workspace = os.path.join(self.root, 'workspace')
        os.makedirs(os.path.join(self.workspace, 'sub'))
        self.target = os.path.join(self.workspace, '対象.py')
        self.other = os.path.join(self.workspace, 'sub', 'other.py')
        for path in (self.target, self.other):
            with open(path, 'w', encoding='utf-8') as file:
                file.write('a\n')

    def payload(self, tool_call, session_id=None):
        return {
            'sessionId': session_id or self.session_id,
            'cwd': self.workspace,
            'toolName': tool_call[0],
            'toolArgs': tool_call[1],
        }

    def run_hook(self, path, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=10,
        )

    def invoke(self, tool_call, session_id=None):
        return self.run_hook(_hook_path, self.payload(tool_call, session_id=session_id))

    def view(self, path, session_id=None):
        # 閲覧の記録は、実際の記録側hookを通して作る。
        result = self.run_hook(_record_hook_path, {
            'sessionId': session_id or self.session_id, 'timestamp': 0, 'cwd': self.workspace,
            'toolName': 'view', 'toolArgs': {'path': path},
            'toolResult': {'resultType': 'success', 'textResultForLlm': '1. a'},
        })
        self.assertEqual((result.returncode, result.stderr), (0, b''))

    def assert_allowed(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_denied(self, result, files):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny')
        self.assertEqual(set(output), {'permissionDecision', 'permissionDecisionReason'})
        self.assertIn(f'not read the following existing files in this session: {files}.', output['permissionDecisionReason'])

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'preToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/preToolUse/require-read-before-edit/require-read-before-edit.py'
        entries = [
            entry for entry in configuration['hooks']['preToolUse']
            if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            with self.subTest(tool_name=tool_name):
                self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        for tool_name in ('bash', 'view', 'skill'):
            with self.subTest(tool_name=tool_name):
                self.assertNotRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')

    def test_denies_unread_file_only_once(self):
        edit = ('edit', {'path': '対象.py', 'old_str': 'a', 'new_str': 'b'})
        self.assert_denied(self.invoke(edit), '対象.py')
        self.assert_allowed(self.invoke(edit))

    def test_allows_file_read_or_edited_earlier(self):
        self.view(self.target)
        self.assert_allowed(self.invoke(('edit', {'path': self.target, 'old_str': 'a', 'new_str': 'b'})))
        # 編集の成功も記録される。
        result = self.run_hook(_record_hook_path, {
            'sessionId': self.session_id, 'timestamp': 0, 'cwd': self.workspace,
            'toolName': 'create', 'toolArgs': {'path': 'sub/other.py', 'file_text': 'a\n'},
            'toolResult': {'resultType': 'success', 'textResultForLlm': 'Created.'},
        })
        self.assertEqual(result.returncode, 0)
        self.assert_allowed(self.invoke(('insert', {'path': self.other, 'insert_line': 1})))

    def test_tool_names_and_argument_formats(self):
        patch_text = '*** Begin Patch\n*** Update File: 対象.py\n@@\n-a\n+b\n*** End Patch\n'
        cases = (
            ('edit', {'path': self.target}),
            ('functions.insert', {'path': './sub/../対象.py', 'insert_line': 1}),
            ('create', {'path': '対象.py', 'file_text': 'overwrite'}),
            ('str_replace_editor', json.dumps({'command': 'str_replace', 'path': '対象.py'})),
            ('apply_patch', {'input': patch_text}),
            ('apply_patch', patch_text),
        )
        for tool_name, tool_args in cases:
            with self.subTest(tool_name=tool_name, tool_args=type(tool_args).__name__):
                self.assert_denied(self.invoke((tool_name, tool_args), session_id=str(uuid.uuid4())), '対象.py')

    def test_allows_edits_that_need_no_prior_read(self):
        patch_text = (
            '*** Begin Patch\n*** Add File: 対象.py\n+x\n*** Delete File: sub/other.py\n'
            '*** Update File: missing.py\n@@\n-a\n+b\n*** End Patch\n'
        )
        cases = (
            ('create', {'path': 'new.py', 'file_text': 'x'}),
            ('edit', {'path': 'missing.py', 'old_str': 'a', 'new_str': 'b'}),
            ('str_replace_editor', {'command': 'view', 'path': '対象.py'}),
            ('apply_patch', patch_text),
            ('view', {'path': '対象.py'}),
            ('bash', {'command': 'cat 対象.py'}),
            ('edit', {}),
            ('edit', None),
            ('apply_patch', 'not a patch'),
        )
        for tool_call in cases:
            with self.subTest(tool_call=tool_call):
                self.assert_allowed(self.invoke(tool_call))
        self.assertFalse(os.path.exists(self.state_directory))

    def test_patch_files_are_checked_together(self):
        self.view(self.other)
        third = os.path.join(self.workspace, 'third.py')
        with open(third, 'w', encoding='utf-8') as file:
            file.write('a\n')
        patch_text = (
            '*** Begin Patch\n*** Update File: 対象.py\n@@\n-a\n+b\n'
            '*** Update File: other.py\n@@\n-a\n+b\n*** End Patch\n'
        )
        self.assert_denied(self.invoke(('apply_patch', patch_text)), '対象.py')
        patch_text = (
            '*** Begin Patch\n*** Update File: 対象.py\n@@\n-a\n+b\n'
            '*** Update File: third.py\n@@\n-a\n+b\n*** End Patch\n'
        )
        self.assert_denied(self.invoke(('apply_patch', patch_text)), 'third.py')

    def test_lists_multiple_files_and_outside_paths(self):
        outside = os.path.join(self.root, 'outside.py')
        with open(outside, 'w', encoding='utf-8') as file:
            file.write('a\n')
        result = self.invoke(('apply_patch', (
            f'*** Begin Patch\n*** Update File: {outside}\n@@\n-a\n+b\n'
            '*** Update File: sub/other.py\n@@\n-a\n+b\n'
            '*** Update File: 対象.py\n@@\n-a\n+b\n*** End Patch\n'
        )))
        # cwd配下のファイルは、OSによらず `/` 区切りで示される。
        files = sorted([os.path.normcase(outside), 'sub/other.py', '対象.py'], key=lambda path: (
            os.path.normcase(os.path.realpath(os.path.join(self.workspace, path)))
        ))
        self.assert_denied(result, ', '.join(files))

    def test_sessions_are_isolated_and_uuid_is_normalized(self):
        self.view(self.target)
        edit = ('edit', {'path': self.target})
        self.assert_denied(self.invoke(edit, session_id=str(uuid.uuid4())), '対象.py')
        self.assert_allowed(self.invoke(edit, session_id=' {' + self.session_id.upper() + '} '))

    def test_invalid_input_reports_error(self):
        base = self.payload(('edit', {'path': self.target}))
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'true', b'{"cwd":"\xff"}']
        for field in ('sessionId', 'cwd', 'toolName', 'toolArgs'):
            payload = base.copy()
            del payload[field]
            invalid.append(json.dumps(payload).encode('utf-8'))
        for field in ('sessionId', 'cwd'):
            for value in ('', ' \t', None, 3, False, [], {}):
                invalid.append(json.dumps({**base, field: value}).encode('utf-8'))
        for value in (None, '', ' \t', 3, [], {}):
            invalid.append(json.dumps({**base, 'toolName': value}).encode('utf-8'))
        for changes in ({'cwd': 'relative'}, {'sessionId': '../bad'}):
            invalid.append(json.dumps({**base, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.run_hook(_hook_path, raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to check read-before-edit:', result.stderr)
        self.assertFalse(os.path.exists(self.state_directory))

    def test_corrupt_state_reports_error(self):
        os.makedirs(self.state_directory)
        with open(self.state_path, 'wb') as file:
            file.write(b'not a SQLite database')
        result = self.invoke(('edit', {'path': self.target}))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to check read-before-edit:', result.stderr)

    def test_concurrent_checks_deny_once(self):
        raw = json.dumps(self.payload(('edit', {'path': self.target})), ensure_ascii=False).encode('utf-8')
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
        denials = 0
        for process in processes:
            output, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, error.decode('utf-8'))
            self.assertEqual(error, b'')
            if output:
                denials += 1
                self.assertEqual(json.loads(output)['permissionDecision'], 'deny')
        self.assertEqual(denials, 1)

    @staticmethod
    def finish_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate()


if __name__ == '__main__':
    run_tests()
