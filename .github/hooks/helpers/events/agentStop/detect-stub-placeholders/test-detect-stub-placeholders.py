import json
import os
import subprocess
import sys
import tempfile
import unittest


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'detect-stub-placeholders.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


HOOK_TIMESTAMP = 1777777777000
REASON_PREFIX = 'The changes in this request leave placeholder code'


def user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def tool(name, arguments, call_id='c1'):
    return {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': name, 'arguments': arguments}}


def agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': HOOK_TIMESTAMP}}}


class DetectStubPlaceholdersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-stub-placeholders-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.workspace = os.path.join(self.root, 'workspace')
        os.makedirs(self.workspace)
        self.transcript_path = os.path.join(self.root, 'events.jsonl')
        self.payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': HOOK_TIMESTAMP,
            'cwd': self.workspace,
            'transcriptPath': self.transcript_path,
        }

    def write_file(self, path, content):
        full_path = os.path.join(self.workspace, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def invoke(self, events=None, payload=None, raw=None, arguments=()):
        if events is not None:
            with open(self.transcript_path, 'w', encoding='utf-8', newline='\n') as file:
                for event in [*events, agent_stop_start()]:
                    file.write(json.dumps(event, ensure_ascii=False) + '\n')
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def created(self, path, content, response='実装した。'):
        # ファイルを作成した依頼のトランスクリプト。
        self.write_file(path, content)
        return [user('実装して'), tool('create', {'path': path, 'file_text': content}), assistant(response)]

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_block(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith(REASON_PREFIX))
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        entries = configuration['hooks']['agentStop']
        relative_path = '.github/hooks/helpers/events/agentStop/detect-stub-placeholders/detect-stub-placeholders.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_placeholders(self):
        for path, line, kind in [
            ('src/Foo.cs', 'throw new NotImplementedException();', 'NotImplementedException'),
            ('src/Foo.cs', 'throw new System.NotImplementedException("later");', 'NotImplementedException'),
            ('foo.py', 'raise NotImplementedError', 'NotImplementedError'),
            ('foo.ts', "throw new Error('Not implemented');", 'not-implemented error'),
            ('Foo.java', 'throw new UnsupportedOperationException("TODO");', 'not-implemented error'),
            ('foo.go', 'panic("not implemented")', 'not-implemented panic'),
            ('foo.rs', 'todo!()', 'todo macro'),
            ('foo.rs', 'unimplemented!("x")', 'todo macro'),
            ('Foo.kt', 'fun f(): Int = TODO()', 'todo macro'),
            ('foo.py', '# TODO: handle errors', 'TODO comment'),
            ('Foo.cs', '// TODO validate input', 'TODO comment'),
            ('foo.sql', '-- TBD', 'TODO comment'),
            ('Foo.cs', '// 後で実装する', 'deferred implementation comment'),
            ('foo.js', '/* implement later */', 'deferred implementation comment'),
        ]:
            with self.subTest(line=line):
                events = self.created(path, f'line one\n    {line}\n')
                self.assert_block(self.invoke(events), f'- {path}: {kind} ("{line}")')

    def test_edit_and_patch_arguments(self):
        self.write_file('a.cs', 'int F() {\n    throw new NotImplementedException();\n}\n')
        self.write_file('b.py', 'def f():\n    raise NotImplementedError\n')
        patch = '*** Begin Patch\n*** Update File: b.py\n def f():\n-    return 1\n+    raise NotImplementedError\n*** End Patch'
        events = [
            user('実装して'),
            tool('functions.edit', json.dumps({
                'path': 'a.cs', 'old_str': 'return 1;', 'new_str': 'throw new NotImplementedException();',
            })),
            tool('apply_patch', patch, call_id='c2'),
            assistant('Done.'),
        ]
        self.assert_block(self.invoke(events), '- a.cs: NotImplementedException', '- b.py: NotImplementedError')

    def test_ignores_existing_removed_and_excluded_placeholders(self):
        self.write_file('kept.cs', 'throw new NotImplementedException();\nint x;\n')
        self.write_file('fixed.py', 'def f():\n    return 1\n')
        for path in ('notes.md', 'config.json'):
            self.write_file(path, '# TODO: later\n')
        events = [
            user('直して'),
            # 置換前の内容にもある行は、元からあるものとみなす。
            tool('edit', {
                'path': 'kept.cs', 'old_str': 'throw new NotImplementedException();',
                'new_str': 'throw new NotImplementedException();\nint x;',
            }),
            # 後の編集でファイルから消えた仮実装は対象外。
            tool('create', {'path': 'fixed.py', 'file_text': 'def f():\n    raise NotImplementedError\n'}, 'c2'),
            tool('edit', {'path': 'notes.md', 'old_str': 'a', 'new_str': '# TODO: later'}, 'c3'),
            tool('create', {'path': 'config.json', 'file_text': '# TODO: later'}, 'c4'),
            # 閲覧と、削除されたファイルへの書き込みは対象外。
            tool('str_replace_editor', {'command': 'view', 'path': 'kept.cs', 'new_str': '# TODO'}, 'c5'),
            tool('create', {'path': 'deleted.py', 'file_text': '# TODO'}, 'c6'),
            tool('bash', {'command': 'echo "# TODO" > x.py'}, 'c7'),
            assistant('直した。'),
        ]
        self.assert_silent(self.invoke(events))

    def test_ignores_fixme_comments(self):
        self.assert_silent(self.invoke(self.created('a.py', '# FIXME: overflow when n is negative\n')))

    def test_allows_disclosed_placeholders(self):
        for response in (
            'Parseは未実装のまま残した。',
            '`Save` はスタブにしてある。',
            'TODOを1件残した。',
            'The export path is not implemented yet.',
            'I left a stub for the parser.',
        ):
            with self.subTest(response=response):
                self.assert_silent(self.invoke(self.created('a.cs', 'throw new NotImplementedException();\n', response)))

    def test_only_latest_request_is_checked(self):
        events = [
            *self.created('a.cs', 'throw new NotImplementedException();\n', 'Parseは未実装。'),
            user('READMEを直して'),
            assistant('直した。'),
        ]
        self.assert_silent(self.invoke(events))

    def test_blocks_only_once_per_request(self):
        events = self.created('a.cs', 'throw new NotImplementedException();\n')
        reason = self.assert_block(self.invoke(events))
        self.assert_silent(self.invoke([*events, user(reason, delivery='queued'), assistant('実装した。')]))
        # 割り込みのメッセージは依頼の区切りにならない。
        self.assert_block(self.invoke([*events, user('急いで', delivery='queued'), assistant('実装した。')]))

    def test_skips_subagents_and_turns_without_response(self):
        self.assert_silent(self.invoke(
            self.created('a.cs', 'throw new NotImplementedException();\n'), payload={**self.payload, 'agentId': 'sub'},
        ))
        self.assert_silent(self.invoke([user('実装して'), tool('create', {'path': 'a.cs', 'file_text': 'x'})]))

    def test_limits_findings(self):
        content = ''.join(f'# TODO item {index}\n' for index in range(12))
        reason = self.assert_block(self.invoke(self.created('a.py', content)), 'item 9')
        self.assertNotIn('item 10', reason)

    def test_invalid_input(self):
        self.write_file('a.txt', '')
        for name, raw in [
            ('empty', b''),
            ('broken json', b'{'),
            ('invalid utf-8', b'\xff'),
            ('not object', b'[]'),
            ('missing cwd', json.dumps({'transcriptPath': self.transcript_path}).encode('utf-8')),
            ('relative cwd', json.dumps({'cwd': 'x', 'transcriptPath': self.transcript_path}).encode('utf-8')),
            ('missing transcript', json.dumps({'cwd': self.workspace}).encode('utf-8')),
            ('transcript not file', json.dumps({'cwd': self.workspace, 'transcriptPath': self.workspace}).encode('utf-8')),
        ]:
            with self.subTest(name=name):
                result = self.invoke(raw=raw)
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect stub placeholders:', result.stderr)
        with open(self.transcript_path, 'w', encoding='utf-8') as file:
            file.write('{broken\n')
        result = self.invoke()
        self.assertEqual((result.returncode, result.stdout), (2, b''))

    def test_invalid_definitions(self):
        valid_placeholders = [{'name': 'x', 'pattern': 'x'}]
        for name, content in [
            ('not object', '[]'),
            ('broken json', '{'),
            ('missing excluded', json.dumps({'placeholderPatterns': valid_placeholders, 'disclosurePatterns': ['x']})),
            ('empty placeholders', json.dumps({
                'excludedFilePatterns': ['x'], 'placeholderPatterns': [], 'disclosurePatterns': ['x'],
            })),
            ('placeholder without pattern', json.dumps({
                'excludedFilePatterns': ['x'], 'placeholderPatterns': [{'name': 'x'}], 'disclosurePatterns': ['x'],
            })),
            ('invalid regex', json.dumps({
                'excludedFilePatterns': ['x'], 'placeholderPatterns': valid_placeholders, 'disclosurePatterns': ['('],
            })),
        ]:
            with self.subTest(name=name):
                definitions_path = os.path.join(self.root, 'definitions.json')
                with open(definitions_path, 'w', encoding='utf-8') as file:
                    file.write(content)
                result = self.invoke(
                    self.created('a.cs', 'x\n'), arguments=('--definitions-path', definitions_path),
                )
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect stub placeholders:', result.stderr)


if __name__ == '__main__':
    run_tests()
