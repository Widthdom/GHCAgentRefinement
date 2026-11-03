import json
import os
import subprocess
import sys
import tempfile
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-stub-placeholders.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


_HOOK_TIMESTAMP = 1777777777000
_REASON_PREFIX = 'The changes in this request leave placeholder code'


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _tool(name, arguments, call_id='c1'):
    return {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': name, 'arguments': arguments}}


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


class DetectStubPlaceholdersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-stub-placeholders-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__workspace = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__workspace)
        self.__transcript_path = os.path.join(self.__root, 'events.jsonl')
        self.__payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': _HOOK_TIMESTAMP,
            'cwd': self.__workspace,
            'transcriptPath': self.__transcript_path,
        }

    def __write_file(self, path, content):
        full_path = os.path.join(self.__workspace, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __invoke(self, events=None, payload=None, raw=None, arguments=()):
        if events is not None:
            with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
                for event in [*events, _agent_stop_start()]:
                    file.write(json.dumps(event, ensure_ascii=False) + '\n')
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def __created(self, path, content, response='実装した。'):
        # ファイルを作成した依頼のトランスクリプト。
        self.__write_file(path, content)
        return [_user('実装して'), _tool('create', {'path': path, 'file_text': content}), _assistant(response)]

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_block(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith(_REASON_PREFIX))
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
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
                events = self.__created(path, f'line one\n    {line}\n')
                self.__assert_block(self.__invoke(events), f'- {path}: {kind} ("{line}")')

    def test_edit_and_patch_arguments(self):
        self.__write_file('a.cs', 'int F() {\n    throw new NotImplementedException();\n}\n')
        self.__write_file('b.py', 'def f():\n    raise NotImplementedError\n')
        patch = '*** Begin Patch\n*** Update File: b.py\n def f():\n-    return 1\n+    raise NotImplementedError\n*** End Patch'
        events = [
            _user('実装して'),
            _tool('functions.edit', json.dumps({
                'path': 'a.cs', 'old_str': 'return 1;', 'new_str': 'throw new NotImplementedException();',
            })),
            _tool('apply_patch', patch, call_id='c2'),
            _assistant('Done.'),
        ]
        self.__assert_block(self.__invoke(events), '- a.cs: NotImplementedException', '- b.py: NotImplementedError')

    def test_ignores_existing_removed_and_excluded_placeholders(self):
        self.__write_file('kept.cs', 'throw new NotImplementedException();\nint x;\n')
        self.__write_file('fixed.py', 'def f():\n    return 1\n')
        for path in ('notes.md', 'config.json'):
            self.__write_file(path, '# TODO: later\n')
        events = [
            _user('直して'),
            # 置換前の内容にもある行は、元からあるものとみなす。
            _tool('edit', {
                'path': 'kept.cs', 'old_str': 'throw new NotImplementedException();',
                'new_str': 'throw new NotImplementedException();\nint x;',
            }),
            # 後の編集でファイルから消えた仮実装は対象外。
            _tool('create', {'path': 'fixed.py', 'file_text': 'def f():\n    raise NotImplementedError\n'}, 'c2'),
            _tool('edit', {'path': 'notes.md', 'old_str': 'a', 'new_str': '# TODO: later'}, 'c3'),
            _tool('create', {'path': 'config.json', 'file_text': '# TODO: later'}, 'c4'),
            # 閲覧と、削除されたファイルへの書き込みは対象外。
            _tool('str_replace_editor', {'command': 'view', 'path': 'kept.cs', 'new_str': '# TODO'}, 'c5'),
            _tool('create', {'path': 'deleted.py', 'file_text': '# TODO'}, 'c6'),
            _tool('bash', {'command': 'echo "# TODO" > x.py'}, 'c7'),
            _assistant('直した。'),
        ]
        self.__assert_silent(self.__invoke(events))

    def test_ignores_fixme_comments(self):
        self.__assert_silent(self.__invoke(self.__created('a.py', '# FIXME: overflow when n is negative\n')))

    def test_allows_disclosed_placeholders(self):
        for response in (
            'Parseは未実装のまま残した。',
            '`Save` はスタブにしてある。',
            'TODOを1件残した。',
            'The export path is not implemented yet.',
            'I left a stub for the parser.',
        ):
            with self.subTest(response=response):
                self.__assert_silent(self.__invoke(self.__created('a.cs', 'throw new NotImplementedException();\n', response)))

    def test_only_latest_request_is_checked(self):
        events = [
            *self.__created('a.cs', 'throw new NotImplementedException();\n', 'Parseは未実装。'),
            _user('READMEを直して'),
            _assistant('直した。'),
        ]
        self.__assert_silent(self.__invoke(events))

    def test_blocks_only_once_per_request(self):
        events = self.__created('a.cs', 'throw new NotImplementedException();\n')
        reason = self.__assert_block(self.__invoke(events))
        self.__assert_silent(self.__invoke([*events, _user(reason, delivery='queued'), _assistant('実装した。')]))
        # 割り込みのメッセージは依頼の区切りにならない。
        self.__assert_block(self.__invoke([*events, _user('急いで', delivery='queued'), _assistant('実装した。')]))

    def test_skips_subagents_and_turns_without_response(self):
        self.__assert_silent(self.__invoke(
            self.__created('a.cs', 'throw new NotImplementedException();\n'), payload={**self.__payload, 'agentId': 'sub'},
        ))
        self.__assert_silent(self.__invoke([_user('実装して'), _tool('create', {'path': 'a.cs', 'file_text': 'x'})]))

    def test_limits_findings(self):
        content = ''.join(f'# TODO item {index}\n' for index in range(12))
        reason = self.__assert_block(self.__invoke(self.__created('a.py', content)), 'item 9')
        self.assertNotIn('item 10', reason)

    def test_invalid_input(self):
        self.__write_file('a.txt', '')
        for name, raw in [
            ('empty', b''),
            ('broken json', b'{'),
            ('invalid utf-8', b'\xff'),
            ('not object', b'[]'),
            ('missing cwd', json.dumps({'transcriptPath': self.__transcript_path}).encode('utf-8')),
            ('relative cwd', json.dumps({'cwd': 'x', 'transcriptPath': self.__transcript_path}).encode('utf-8')),
            ('missing transcript', json.dumps({'cwd': self.__workspace}).encode('utf-8')),
            ('transcript not file', json.dumps({'cwd': self.__workspace, 'transcriptPath': self.__workspace}).encode('utf-8')),
        ]:
            with self.subTest(name=name):
                result = self.__invoke(raw=raw)
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect stub placeholders:', result.stderr)
        with open(self.__transcript_path, 'w', encoding='utf-8') as file:
            file.write('{broken\n')
        result = self.__invoke()
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
                definitions_path = os.path.join(self.__root, 'definitions.json')
                with open(definitions_path, 'w', encoding='utf-8') as file:
                    file.write(content)
                result = self.__invoke(
                    self.__created('a.cs', 'x\n'), arguments=('--definitions-path', definitions_path),
                )
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect stub placeholders:', result.stderr)


if __name__ == '__main__':
    run_tests()
