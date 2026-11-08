import json
import os
import subprocess
import sys
import tempfile
import time
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-empty-promises.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


_HOOK_TIMESTAMP = 1777777777000
_REASON_PREFIX = 'The final response promises work for later'


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _edit(call_id, arguments, tool_name='edit', success=True):
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': tool_name, 'arguments': arguments}},
        {'type': 'tool.execution_complete', 'data': {'toolCallId': call_id, 'success': success}},
    ]


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


class DetectEmptyPromisesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-empty-promises-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = temporary.name
        self.__transcript_path = os.path.join(self.__root, 'events.jsonl')
        self.__payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': _HOOK_TIMESTAMP,
            'cwd': self.__root,
            'transcriptPath': self.__transcript_path,
        }

    def __write_transcript(self, events):
        with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            for event in events:
                file.write(json.dumps(event, ensure_ascii=False) + '\n')

    def __invoke(self, events=None, payload=None, raw=None):
        if events is not None:
            self.__write_transcript([*events, _agent_stop_start()])
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
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
        self.assertIn('Do the deferred work now', output['reason'])
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_blocks_deferred_promises(self):
        responses = (
            ('修正した。残りのlint警告は後で直します。', '- 残りのlint警告は後で直します。'),
            ('テストの追加は別途対応する予定。', '- テストの追加は別途対応する予定。'),
            ('今後リファクタリングしていきます。', '- 今後リファクタリングしていきます。'),
            ('README.md を更新した。後ほど確認します。', '- 後ほど確認します。'),
            ('型の整理は別のPRで対応しよう。', '- 型の整理は別のPRで対応しよう。'),
            ("Done. I'll fix the remaining warnings later.", "- I'll fix the remaining warnings later."),
            ('I will address this in a follow-up PR.', '- I will address this in a follow-up PR.'),
            ('We are going to clean up the helpers separately.', '- We are going to clean up the helpers separately.'),
            ("We'll follow up on the docs.", "- We'll follow up on the docs."),
            ('The cleanup will be handled separately.', '- The cleanup will be handled separately.'),
        )
        for response, fragment in responses:
            with self.subTest(response=response):
                reason = self.__assert_block(self.__invoke([_user('直して'), _assistant(response)]), fragment)
                self.assertNotIn('- 修正した。', reason)
                self.assertNotIn('- Done.', reason)

    def test_ignores_offers_explanations_and_code(self):
        responses = (
            '修正した。',
            '必要なら後で追加する。',
            'ご希望があれば別途対応する。',
            '後で対応するかどうかは任せる。',
            '次回修正する必要はない。',
            '設定は後で読み込まれる。',
            '後で確認したところ問題なかった。',
            '今後の課題として残す。',
            '後で直しますか？',
            "If you want, I'll add docs later.",
            'Should I fix it later?',
            "Let me know and I'll update it later.",
            'The value is computed later.',
            '`後で修正する` というコメントを消した。',
            '「後で直す」と書くと差し戻す。',
            '『別途対応する』という表現を検出する。',
            'It rejects "I\'ll fix it later" in responses.',
            'It rejects “we will address this later” too.',
            '例:\n```\n後で直します。\n```\n',
        )
        for response in responses:
            with self.subTest(response=response):
                self.__assert_silent(self.__invoke([_user('直して'), _assistant(response)]))

    def test_allows_when_fixme_was_written(self):
        edits = (
            _edit('e1', {'path': 'src/a.py', 'old_str': 'x = 1', 'new_str': '# FIXME: 負の値で落ちる\nx = 1'}),
            _edit('e1', {'path': 'src/a.cs', 'file_text': '// FIXME: nullで落ちる\n'}, 'create'),
            _edit('e1', {'input': '*** Begin Patch\n*** Update File: src/a.go\n@@\n+// FIXME: 競合する\n*** End Patch'}, 'apply_patch'),
        )
        for edit_events in edits:
            with self.subTest(edit_events=edit_events):
                events = [_user('直して'), *edit_events, _assistant('依頼範囲外の問題は後で対応する予定。')]
                self.__assert_silent(self.__invoke(events))

    def test_failed_or_unrelated_edits_do_not_exempt(self):
        edits = (
            _edit('e1', {'path': 'src/a.py', 'new_str': '# FIXME: 落ちる'}, success=False),
            _edit('e1', {'path': 'src/a.py', 'old_str': '# FIXME: 落ちる', 'new_str': ''}),
            _edit('e1', {'path': 'src/a.py', 'new_str': '# fixme later'}),
        )
        for edit_events in edits:
            with self.subTest(edit_events=edit_events):
                events = [_user('直して'), *edit_events, _assistant('後で対応する予定。')]
                self.__assert_block(self.__invoke(events))

    def test_lists_promises_with_limit(self):
        response = '\n'.join(f'項目{index}は後で直します。' for index in range(7))
        reason = self.__assert_block(self.__invoke([_user('直して'), _assistant(response)]), '- 項目4は後で直します。', '... and 2 more')
        self.assertNotIn('項目5', reason)

    def test_long_promise_is_truncated(self):
        promise = '後で直します' + 'あ' * 300
        reason = self.__assert_block(self.__invoke([_user('直して'), _assistant(promise)]))
        self.assertIn(promise[:200] + '...', reason)
        self.assertNotIn(promise, reason)

    def test_only_events_after_latest_user_request_count(self):
        events = [
            _user('前の依頼'), *_edit('e1', {'path': 'src/a.py', 'new_str': '# FIXME: 落ちる'}), _assistant('修正した。'),
            _user('次の依頼'), _assistant('後で直します。'),
        ]
        self.__assert_block(self.__invoke(events))
        self.__assert_silent(self.__invoke([_user('前の依頼'), _assistant('後で直します。'), _user('次の依頼'), _assistant('直した。')]))

    def test_blocks_only_once_per_request(self):
        first = [_user('直して'), _assistant('後で直します。')]
        reason = self.__assert_block(self.__invoke(first))
        self.__assert_silent(self.__invoke([*first, _user(reason, 'queued'), _assistant('後で直します。')]))
        self.__assert_block(self.__invoke([*first, _user('割り込み', 'queued'), _assistant('後で直します。')]))

    def test_skips_subagents_and_missing_response(self):
        self.__assert_silent(self.__invoke([_user('直して')]))
        self.__write_transcript([_user('直して'), _assistant('後で直します。')])
        self.__assert_silent(self.__invoke(payload={**self.__payload, 'agentId': 'child-agent'}))

    def test_waits_for_agent_stop_event(self):
        self.__write_transcript([_user('直して')])
        process = subprocess.Popen(
            [sys.executable, _hook_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        process.stdin.write(json.dumps(self.__payload).encode('utf-8'))
        process.stdin.close()
        process.stdin = None
        time.sleep(0.5)
        with open(self.__transcript_path, 'a', encoding='utf-8', newline='\n') as file:
            for event in (_assistant('後で直します。'), _agent_stop_start()):
                file.write(json.dumps(event, ensure_ascii=False) + '\n')
        output, error = process.communicate(timeout=15)
        self.__assert_block(subprocess.CompletedProcess([], process.returncode, output, error))

    def test_invalid_input_reports_error(self):
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'\xff']
        for value in (None, '', ' ', 3, [], os.path.join(self.__root, 'missing.jsonl'), self.__root):
            invalid.append(json.dumps({**self.__payload, 'transcriptPath': value}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertTrue(result.stderr)

    def test_invalid_transcript_reports_error(self):
        for content in ('{', '[]\n', '{"type":"user.message","data":[]}\n'):
            with self.subTest(content=content):
                with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
                    file.write(content)
                result = self.__invoke(payload={**self.__payload, 'timestamp': None})
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to inspect deferred promises:', result.stderr)


if __name__ == '__main__':
    run_tests()
