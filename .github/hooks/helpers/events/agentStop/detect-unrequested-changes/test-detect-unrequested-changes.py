import json
import os
import subprocess
import sys
import tempfile
import unittest


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'detect-unrequested-changes.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


HOOK_TIMESTAMP = 1777777777000
REASON_PREFIX = 'Some changes in this request are not clearly part of what the user asked for'
WHITESPACE_ONLY = 'whitespace-only change'
DELETED = 'deleted although'
MOVED = 'moved to'
UNMENTIONED = 'neither the request nor the responses mention this file'


def user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def tool(name, arguments, call_id='c1'):
    return {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': name, 'arguments': arguments}}


def edit(path, old='a', new='b', call_id='c1'):
    return tool('edit', {'path': path, 'old_str': old, 'new_str': new}, call_id)


def agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': HOOK_TIMESTAMP}}}


class DetectUnrequestedChangesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-unrequested-changes-test-')
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
        relative_path = '.github/hooks/helpers/events/agentStop/detect-unrequested-changes/detect-unrequested-changes.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_unmentioned_files(self):
        events = [
            user('src/app.py のバグを直して'),
            edit('src/app.py'),
            edit('src/util/helpers.py', call_id='c2'),
            assistant('src/app.py を直した。'),
        ]
        reason = self.assert_block(self.invoke(events), f'- src/util/helpers.py: changed, but {UNMENTIONED}')
        self.assertNotIn('- src/app.py', reason)

    def test_files_mentioned_in_request_or_responses_are_allowed(self):
        cases = [
            # ファイル名
            ([user('a.py を直して'), edit('lib/a.py'), assistant('直した。')]),
            # 応答でのパスの言及（大文字小文字は区別しない）
            ([user('直して'), edit('lib/Config.cs'), assistant('`lib/config.cs` を直した。')]),
            # 途中の応答での言及
            ([user('直して'), assistant('まず b.py を直す。'), edit('b.py'), assistant('直した。')]),
            # 拡張子を除いた名前
            ([user('READMEを直して'), edit('README.md'), assistant('直した。')]),
            # 親ディレクトリ名
            ([user('detect-foo hookを作って'), edit('hooks/detect-foo/test-detect-foo.py'), assistant('作った。')]),
            # 割り込みの指示での言及
            ([user('直して'), user('ついでに notes.py も', delivery='queued'), edit('notes.py'), assistant('直した。')]),
        ]
        for events in cases:
            with self.subTest(events=events[0]['data']['content']):
                self.assert_silent(self.invoke(events))

    def test_short_name_parts_are_not_enough(self):
        # 短い名前は偶然一致しやすいため、拡張子を除いた名前や親ディレクトリ名では判定しない。
        events = [user('aの値を直して'), edit('src/a.py'), assistant('直した。')]
        self.assert_block(self.invoke(events), f'- src/a.py: changed, but {UNMENTIONED}')

    def test_whitespace_only_changes(self):
        cases = [
            ('edit', {'path': 'a.py', 'old_str': 'x = 1\n', 'new_str': '    x = 1\n'}),
            ('functions.edit', json.dumps({'path': 'a.py', 'old_str': 'f(a,b)', 'new_str': 'f(a, b)'})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'a.py', 'old_str': 'x = 1  ', 'new_str': 'x = 1'}),
            ('apply_patch', '*** Begin Patch\n*** Update File: a.py\n@@\n-if x:\n-  y()\n+if x:\n+    y()\n*** End Patch'),
        ]
        for tool_name, arguments in cases:
            with self.subTest(tool_name=tool_name):
                events = [user('a.py の不具合を直して'), tool(tool_name, arguments), assistant('a.py を直した。')]
                self.assert_block(self.invoke(events), f'- a.py: {WHITESPACE_ONLY}')

    def test_whitespace_changes_are_allowed_when_requested_or_not_whitespace_only(self):
        for request, old, new in [
            ('a.py のインデントを直して', 'x = 1', '    x = 1'),
            ('Format a.py', 'f(a,b)', 'f(a, b)'),
            ('a.py の不具合を直して', 'x = 1', 'x = 2'),
            ('a.py に追記して', '', '    '),
        ]:
            with self.subTest(request=request, new=new):
                events = [user(request), edit('a.py', old, new), assistant('a.py を直した。')]
                self.assert_silent(self.invoke(events))

    def test_deletions_and_moves(self):
        patch = (
            '*** Begin Patch\n*** Delete File: old.py\n'
            '*** Update File: a.py\n*** Move to: lib/a.py\n@@\n-x = 1\n+x = 2\n*** End Patch'
        )
        events = [user('old.py と a.py の不具合を直して'), tool('apply_patch', patch), assistant('old.py と a.py を直した。')]
        self.assert_block(self.invoke(events), f'- old.py: {DELETED}', f'- a.py: {MOVED} lib/a.py')
        for request in ('old.py を削除して a.py を移動して', 'Remove old.py and move a.py'):
            with self.subTest(request=request):
                self.assert_silent(self.invoke([user(request), tool('apply_patch', patch), assistant('対応した。')]))

    def test_reasons_for_the_same_file_are_combined(self):
        events = [
            user('直して'),
            edit('a.py', 'x=1', 'x = 1'),
            edit('a.py', 'y=1', 'y = 1', call_id='c2'),
            assistant('直した。'),
        ]
        reason = self.assert_block(self.invoke(events), f'- a.py: {WHITESPACE_ONLY}')
        self.assertEqual(reason.count('- a.py:'), 1)
        self.assertEqual(reason.count(WHITESPACE_ONLY), 1)
        self.assertIn(UNMENTIONED, reason)

    def test_ignores_excluded_files_views_and_shell(self):
        events = [
            user('直して'),
            edit('package-lock.json'),
            edit('sub/yarn.lock', call_id='c2'),
            tool('str_replace_editor', {'command': 'view', 'path': 'hidden.py'}, 'c3'),
            tool('bash', {'command': 'echo x > hidden.py'}, 'c4'),
            assistant('直した。'),
        ]
        self.assert_silent(self.invoke(events))

    def test_only_latest_request_is_checked(self):
        events = [user('直して'), edit('hidden.py'), assistant('直した。'), user('説明して'), assistant('説明した。')]
        self.assert_silent(self.invoke(events))

    def test_blocks_only_once_per_request(self):
        events = [user('直して'), edit('hidden.py'), assistant('直した。')]
        reason = self.assert_block(self.invoke(events))
        self.assert_silent(self.invoke([*events, user(reason, delivery='queued'), assistant('戻した。')]))
        # 割り込みのメッセージは依頼の区切りにならない。
        self.assert_block(self.invoke([*events, user('急いで', delivery='queued'), assistant('直した。')]))

    def test_skips_subagents_and_turns_without_response(self):
        events = [user('直して'), edit('hidden.py'), assistant('直した。')]
        self.assert_silent(self.invoke(events, payload={**self.payload, 'agentId': 'sub'}))
        self.assert_silent(self.invoke([user('直して'), edit('hidden.py')]))

    def test_limits_findings(self):
        events = [user('直して'), *(edit(f'file{index:02}.py', call_id=f'c{index}') for index in range(12)), assistant('直した。')]
        reason = self.assert_block(self.invoke(events), 'file09.py')
        self.assertNotIn('file10.py', reason)

    def test_invalid_input(self):
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
                self.assertIn(b'Failed to detect unrequested changes:', result.stderr)
        with open(self.transcript_path, 'w', encoding='utf-8') as file:
            file.write('{broken\n')
        result = self.invoke()
        self.assertEqual((result.returncode, result.stdout), (2, b''))

    def test_invalid_definitions(self):
        valid = {'excludedFilePatterns': [], 'formattingRequestPatterns': ['x'], 'removalRequestPatterns': ['x']}
        for name, content in [
            ('not object', '[]'),
            ('broken json', '{'),
            ('missing excluded', json.dumps({**valid, 'excludedFilePatterns': None})),
            ('empty formatting', json.dumps({**valid, 'formattingRequestPatterns': []})),
            ('missing removal', json.dumps({key: value for key, value in valid.items() if key != 'removalRequestPatterns'})),
            ('invalid regex', json.dumps({**valid, 'removalRequestPatterns': ['(']})),
        ]:
            with self.subTest(name=name):
                definitions_path = os.path.join(self.root, 'definitions.json')
                with open(definitions_path, 'w', encoding='utf-8') as file:
                    file.write(content)
                result = self.invoke(
                    [user('直して'), edit('a.py'), assistant('直した。')], arguments=('--definitions-path', definitions_path),
                )
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect unrequested changes:', result.stderr)


if __name__ == '__main__':
    run_tests()
