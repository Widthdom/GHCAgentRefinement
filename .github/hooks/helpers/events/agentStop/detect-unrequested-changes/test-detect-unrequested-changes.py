import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-unrequested-changes.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


_HOOK_TIMESTAMP = 1777777777000
_REASON_PREFIX = 'Some changes in this request are not clearly part of what the user asked for'
_WHITESPACE_ONLY = 'whitespace-only change'
_DELETED = 'deleted although'
_MOVED = 'moved to'
_UNMENTIONED = 'neither the request nor the responses mention this file'


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _tool(name, arguments, call_id='c1', success=True):
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': name, 'arguments': arguments}},
        {'type': 'tool.execution_complete', 'data': {'toolCallId': call_id, 'success': success}},
    ]


def _edit(path, old='a', new='b', call_id='c1'):
    return _tool('edit', {'path': path, 'old_str': old, 'new_str': new}, call_id)


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


class DetectUnrequestedChangesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-unrequested-changes-test-')
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

    def test_blocks_unmentioned_files(self):
        events = [
            _user('src/app.py のバグを直して'),
            *_edit('src/app.py'),
            *_edit('src/util/helpers.py', call_id='c2'),
            _assistant('src/app.py を直した。'),
        ]
        reason = self.__assert_block(self.__invoke(events), f'- src/util/helpers.py: changed, but {_UNMENTIONED}')
        self.assertNotIn('- src/app.py', reason)

    def test_ignores_rejected_failed_and_unfinished_edits(self):
        patch = (
            '*** Begin Patch\n*** Add File: rejected.py\n+x = 1\n'
            '*** Delete File: old.py\n'
            '*** Update File: moved.py\n*** Move to: lib/moved.py\n@@\n-x=1\n+x = 1\n*** End Patch'
        )
        for name, arguments in (
            ('apply_patch', patch),
            ('functions.apply_patch', {'input': patch}),
            ('create', {'path': 'rejected.py', 'file_text': 'x = 1'}),
            ('edit', {'path': 'hidden.py', 'old_str': 'x=1', 'new_str': 'x = 1'}),
        ):
            start, complete = _tool(name, arguments)
            for ending in (
                [],
                [{**complete, 'data': {'toolCallId': 'c1', 'success': False}}],
                [{**complete, 'data': {'toolCallId': 'c1'}}],
                [{**complete, 'data': {'toolCallId': 'c1', 'success': 'true'}}],
            ):
                with self.subTest(name=name, ending=ending):
                    self.__assert_silent(self.__invoke([
                        _user('直して'), start, *ending, _assistant('直した。'),
                    ]))

    def test_matches_completions_to_edits(self):
        rejected_start, rejected_end = _tool('create', {'path': 'rejected.py'}, 'rejected', success=False)
        accepted_start, accepted_end = _tool('create', {'path': 'accepted.py'}, 'accepted')
        events = [
            _user('直して'), rejected_start, accepted_start, accepted_end, rejected_end,
            _assistant('直した。'),
        ]
        reason = self.__assert_block(self.__invoke(events), '- accepted.py:')
        self.assertNotIn('rejected.py', reason)

    def test_ignores_completions_without_current_request_start(self):
        start, complete = _tool('create', {'path': 'hidden.py'})
        for prefix in ([], [_user('前の依頼'), start]):
            with self.subTest(prefix=prefix):
                self.__assert_silent(self.__invoke([
                    *prefix, _user('説明して'), complete, _assistant('説明した。'),
                ]))

    def test_files_mentioned_in_request_or_responses_are_allowed(self):
        cases = [
            # ファイル名
            ([_user('a.py を直して'), *_edit('lib/a.py'), _assistant('直した。')]),
            # 応答でのパスの言及（大文字小文字は区別しない）
            ([_user('直して'), *_edit('lib/Config.cs'), _assistant('`lib/config.cs` を直した。')]),
            # 途中の応答での言及
            ([_user('直して'), _assistant('まず b.py を直す。'), *_edit('b.py'), _assistant('直した。')]),
            # 拡張子を除いた名前
            ([_user('READMEを直して'), *_edit('README.md'), _assistant('直した。')]),
            # 親ディレクトリ名
            ([_user('detect-foo hookを作って'), *_edit('hooks/detect-foo/test-detect-foo.py'), _assistant('作った。')]),
            # 割り込みの指示での言及
            ([_user('直して'), _user('ついでに notes.py も', delivery='queued'), *_edit('notes.py'), _assistant('直した。')]),
        ]
        for events in cases:
            with self.subTest(events=events[0]['data']['content']):
                self.__assert_silent(self.__invoke(events))

    def test_short_name_parts_are_not_enough(self):
        # 短い名前は偶然一致しやすいため、拡張子を除いた名前や親ディレクトリ名では判定しない。
        events = [_user('aの値を直して'), *_edit('src/a.py'), _assistant('直した。')]
        self.__assert_block(self.__invoke(events), f'- src/a.py: changed, but {_UNMENTIONED}')

    def test_whitespace_only_changes(self):
        cases = [
            ('edit', {'path': 'a.py', 'old_str': 'x = 1\n', 'new_str': '    x = 1\n'}),
            ('functions.edit', json.dumps({'path': 'a.py', 'old_str': 'f(a,b)', 'new_str': 'f(a, b)'})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'a.py', 'old_str': 'x = 1  ', 'new_str': 'x = 1'}),
            ('apply_patch', '*** Begin Patch\n*** Update File: a.py\n@@\n-if x:\n-  y()\n+if x:\n+    y()\n*** End Patch'),
        ]
        for tool_name, arguments in cases:
            with self.subTest(tool_name=tool_name):
                events = [_user('a.py の不具合を直して'), *_tool(tool_name, arguments), _assistant('a.py を直した。')]
                self.__assert_block(self.__invoke(events), f'- a.py: {_WHITESPACE_ONLY}')

    def test_whitespace_changes_are_allowed_when_requested_or_not_whitespace_only(self):
        for request, old, new in [
            ('a.py のインデントを直して', 'x = 1', '    x = 1'),
            ('Format a.py', 'f(a,b)', 'f(a, b)'),
            ('a.py の不具合を直して', 'x = 1', 'x = 2'),
            ('a.py に追記して', '', '    '),
        ]:
            with self.subTest(request=request, new=new):
                events = [_user(request), *_edit('a.py', old, new), _assistant('a.py を直した。')]
                self.__assert_silent(self.__invoke(events))

    def test_deletions_and_moves(self):
        patch = (
            '*** Begin Patch\n*** Delete File: old.py\n'
            '*** Update File: a.py\n*** Move to: lib/a.py\n@@\n-x = 1\n+x = 2\n*** End Patch'
        )
        events = [_user('old.py と a.py の不具合を直して'), *_tool('apply_patch', patch), _assistant('old.py と a.py を直した。')]
        self.__assert_block(self.__invoke(events), f'- old.py: {_DELETED}', f'- a.py: {_MOVED} lib/a.py')
        for request in ('old.py を削除して a.py を移動して', 'Remove old.py and move a.py'):
            with self.subTest(request=request):
                self.__assert_silent(self.__invoke([_user(request), *_tool('apply_patch', patch), _assistant('対応した。')]))

    def test_reasons_for_the_same_file_are_combined(self):
        events = [
            _user('直して'),
            *_edit('a.py', 'x=1', 'x = 1'),
            *_edit('a.py', 'y=1', 'y = 1', call_id='c2'),
            _assistant('直した。'),
        ]
        reason = self.__assert_block(self.__invoke(events), f'- a.py: {_WHITESPACE_ONLY}')
        self.assertEqual(reason.count('- a.py:'), 1)
        self.assertEqual(reason.count(_WHITESPACE_ONLY), 1)
        self.assertIn(_UNMENTIONED, reason)

    def test_ignores_excluded_files_views_and_shell(self):
        events = [
            _user('直して'),
            *_edit('package-lock.json'),
            *_edit('sub/yarn.lock', call_id='c2'),
            *_tool('str_replace_editor', {'command': 'view', 'path': 'hidden.py'}, 'c3'),
            *_tool('bash', {'command': 'echo x > hidden.py'}, 'c4'),
            _assistant('直した。'),
        ]
        self.__assert_silent(self.__invoke(events))

    def test_only_latest_request_is_checked(self):
        events = [_user('直して'), *_edit('hidden.py'), _assistant('直した。'), _user('説明して'), _assistant('説明した。')]
        self.__assert_silent(self.__invoke(events))

    def test_blocks_only_once_per_request(self):
        events = [_user('直して'), *_edit('hidden.py'), _assistant('直した。')]
        reason = self.__assert_block(self.__invoke(events))
        self.__assert_silent(self.__invoke([*events, _user(reason, delivery='queued'), _assistant('戻した。')]))
        # 割り込みのメッセージは依頼の区切りにならない。
        self.__assert_block(self.__invoke([*events, _user('急いで', delivery='queued'), _assistant('直した。')]))

    def test_skips_subagents_and_turns_without_response(self):
        events = [_user('直して'), *_edit('hidden.py'), _assistant('直した。')]
        self.__assert_silent(self.__invoke(events, payload={**self.__payload, 'agentId': 'sub'}))
        self.__assert_silent(self.__invoke([_user('直して'), *_edit('hidden.py')]))

    def test_limits_findings(self):
        events = [
            _user('直して'),
            *(event for index in range(12) for event in _edit(f'file{index:02}.py', call_id=f'c{index}')),
            _assistant('直した。'),
        ]
        reason = self.__assert_block(self.__invoke(events), 'file09.py')
        self.assertNotIn('file10.py', reason)

    def test_invalid_input(self):
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
                self.assertIn(b'Failed to detect unrequested changes:', result.stderr)
        with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write('{broken\n')
        result = self.__invoke()
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
                definitions_path = os.path.join(self.__root, 'definitions.json')
                with open(definitions_path, 'w', encoding='utf-8', newline='\n') as file:
                    file.write(content)
                result = self.__invoke(
                    [_user('直して'), *_edit('a.py'), _assistant('直した。')], arguments=('--definitions-path', definitions_path),
                )
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect unrequested changes:', result.stderr)


if __name__ == '__main__':
    _run_tests()
