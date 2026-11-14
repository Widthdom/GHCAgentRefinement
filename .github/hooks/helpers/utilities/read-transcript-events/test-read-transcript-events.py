import json
import os
import re
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'read-transcript-events.py'))
_read_transcript_events = _utility['read_transcript_events']
_wait_for_agent_stop_event = _utility['wait_for_agent_stop_event']
_get_turn_events = _utility['get_turn_events']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_REASON_PREFIX = 'Hook reason'


def _user(content, queued=False):
    data = {'content': content}
    if queued:
        data['delivery'] = 'queued'
    return {'type': 'user.message', 'data': data}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _hook_start(hook_type, timestamp):
    return {'type': 'hook.start', 'data': {'hookType': hook_type, 'input': {'timestamp': timestamp}}}


class _TranscriptTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-read-transcript-events-test-')
        self.addCleanup(temporary.cleanup)
        self.path = os.path.join(temporary.name, 'events.jsonl')

    def write(self, *lines, mode='w', encoding='utf-8'):
        # 文字列はそのまま1行として、それ以外はJSONにして書く。
        with open(self.path, mode, encoding=encoding, newline='') as file:
            file.writelines(
                (line if isinstance(line, str) else json.dumps(line, ensure_ascii=False)) + '\n' for line in lines
            )


class ReadTranscriptEventsTests(_TranscriptTestCase):
    def test_reads_events_skipping_blank_lines_and_bom(self):
        # dataがないイベントは、空のオブジェクトのdataを持つものとして返す。
        for encoding in ('utf-8', 'utf-8-sig'):
            with self.subTest(encoding=encoding):
                self.write('{"type":"a"}', '', '  \r', '{"type":"b","data":{"content":"日本語"}}\r', encoding=encoding)
                self.assertEqual(list(_read_transcript_events(self.path)), [
                    {'type': 'a', 'data': {}}, {'type': 'b', 'data': {'content': '日本語'}},
                ])

    def test_rejects_invalid_lines_with_line_number(self):
        for lines, message in (
            (('{"a":1}', '{"a":'), 'Invalid JSON at line 2'),
            (('{"a":1}', '', '[1]'), 'Transcript event must be a JSON object at line 3.'),
            (('"text"',), 'Transcript event must be a JSON object at line 1.'),
            (('{"a":1}', '{"data":[]}'), 'Event data must be a JSON object at line 2.'),
            (('{"data":null}',), 'Event data must be a JSON object at line 1.'),
        ):
            with self.subTest(lines=lines):
                self.write(*lines)
                with self.assertRaises(ValueError) as context:
                    list(_read_transcript_events(self.path))
                self.assertIn(message, str(context.exception))

    def test_skips_incomplete_lines_only_when_requested(self):
        # 追記中の不完全な行は読み飛ばせるが、JSONとして読めてオブジェクトでない行は異常のままとする。
        self.write('{"type":"a"}', '{"type":"b"')
        self.assertEqual(list(_read_transcript_events(self.path, skip_incomplete_lines=True)), [{'type': 'a', 'data': {}}])
        self.write('{"type":"b"', '[1]')
        with self.assertRaises(ValueError):
            list(_read_transcript_events(self.path, skip_incomplete_lines=True))

    def test_yields_lazily_before_invalid_line(self):
        # 不正な行より前のイベントは、その行を読むまでに受け取れる。
        self.write('{"a":1}', 'broken')
        events = _read_transcript_events(self.path)
        self.assertEqual(next(events), {'a': 1, 'data': {}})
        with self.assertRaises(ValueError):
            next(events)

    def test_missing_file(self):
        with self.assertRaises(OSError):
            list(_read_transcript_events(self.path))

    def test_get_event_time_ms(self):
        # 1_800_000_000_000ミリ秒は2027-01-15T08:00:00Z。
        for timestamp, expected in (
            ('2027-01-15T08:00:00.000Z', 1_800_000_000_000),
            ('2027-01-15T17:00:00.500+09:00', 1_800_000_000_500),
            ('not a time', None),
            (1_800_000_000_000, None),
            (None, None),
        ):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(_utility['get_event_time_ms']({'timestamp': timestamp}), expected)


class WaitForAgentStopEventTests(_TranscriptTestCase):
    def __wait(self, timestamp, sleep=None, monotonic=None):
        sleep = sleep or Mock()
        with (
            patch.object(_utility['time'], 'sleep', sleep),
            patch.object(_utility['time'], 'monotonic', monotonic or Mock(side_effect=range(1000))),
        ):
            _wait_for_agent_stop_event(self.path, timestamp, timeout_seconds=5.0)
        return sleep

    def test_returns_without_waiting_when_recorded(self):
        # 不完全な行・hook.start以外・他のhook・形の異なる入力・timestampの異なるagentStopの後でも、一致するものを見つける。
        self.write(
            '{"type":"assistant.message"', _hook_start('preToolUse', 100), _hook_start('agentStop', 99),
            {'type': 'tool.execution_start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': 100}}},
            {'type': 'hook.start'},
            {'type': 'hook.start', 'data': {'hookType': 'agentStop'}},
            {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': None}},
            {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': 'unexpected'}},
            _hook_start('agentStop', 100),
        )
        self.__wait(100).assert_not_called()

    def test_waits_until_recorded(self):
        self.write(_hook_start('agentStop', 99))
        sleep = Mock(side_effect=lambda _: self.write(_hook_start('agentStop', 100), mode='a'))
        self.__wait(100, sleep).assert_called_once_with(0.05)

    def test_stops_at_deadline(self):
        self.write(_hook_start('agentStop', 99))
        # 開始時刻0で期限は5。確認ごとに時刻を2ずつ進めると、0・2・4で待ち、6で期限を過ぎる。
        sleep = self.__wait(100, monotonic=Mock(side_effect=[0, 0, 2, 4, 6]))
        self.assertEqual(sleep.call_count, 3)

    def test_skips_non_numeric_timestamp(self):
        # timestampを照合できなければファイルを読まずに戻る。
        for timestamp in (None, '100', True):
            with self.subTest(timestamp=timestamp):
                self.__wait(timestamp).assert_not_called()

    def test_accepts_later_agent_stop_event_without_matching_input(self):
        # inputのtimestampが一致しなくても、イベント自体の時刻が入力のtimestamp以降なら書き込み済みとみなす。
        # 1_800_000_000_000ミリ秒は2027-01-15T08:00:00Z。時計の差として1秒前までを含める。
        hook_timestamp = 1_800_000_000_000
        for event_time, recorded in (
            ('2027-01-15T08:00:00.000Z', True),
            ('2027-01-15T07:59:59.500Z', True),
            ('2027-01-15T07:59:58.000Z', False),
            ('not a time', False),
        ):
            with self.subTest(event_time=event_time):
                self.write({
                    'type': 'hook.start', 'timestamp': event_time,
                    'data': {'hookType': 'agentStop', 'input': {'timestamp': 'different-format'}},
                })
                sleep = self.__wait(hook_timestamp, monotonic=Mock(side_effect=[0, 0, 6]))
                self.assertEqual(sleep.call_count, 0 if recorded else 1)

    def test_rejects_invalid_events(self):
        # 不完全な行でない不正なイベントは、待たずに異常とする。
        self.write(_user('request'), {'type': 'hook.start', 'data': []})
        with self.assertRaisesRegex(ValueError, f'^{re.escape("Event data must be a JSON object at line 2.")}$'):
            self.__wait(100)


class GetTurnEventsTests(_TranscriptTestCase):
    def test_splits_turns_at_requests_but_not_at_queued_messages(self):
        self.write(
            _assistant('before'),
            _user('first'),
            {'type': 'tool.execution_start', 'data': {'toolName': 'view'}},
            _user('second'),
            _user('interrupt', queued=True),
            {'type': 'hook.start'},
        )
        self.assertEqual(_get_turn_events(self.path), [
            [('assistant.message', {'content': 'before'})],
            [('user.message', {'content': 'first'}), ('tool.execution_start', {'toolName': 'view'})],
            [
                ('user.message', {'content': 'second'}),
                ('user.message', {'content': 'interrupt', 'delivery': 'queued'}),
                ('hook.start', {}),
            ],
        ])

    def test_always_returns_at_least_one_turn(self):
        for events, expected in (
            ([], [[]]),
            ([_assistant('only')], [[('assistant.message', {'content': 'only'})]]),
            ([_user('request')], [[], [('user.message', {'content': 'request'})]]),
        ):
            with self.subTest(events=events):
                self.write(*events)
                self.assertEqual(_get_turn_events(self.path), expected)

    def test_latest_turn_and_requests_by_hook(self):
        # 差し戻しとみなすのは、最新の依頼の中にある、接頭辞で始まるqueuedのuser.messageだけ。
        for name, events, expected in (
            ('requested twice', [
                _user('request'), _user(_REASON_PREFIX + ': fix', queued=True), _user(_REASON_PREFIX, queued=True),
            ], 2),
            ('other hook', [_user('request'), _user('Other reason', queued=True)], 0),
            ('earlier request', [_user('old'), _user(_REASON_PREFIX, queued=True), _user('new')], 0),
            ('not queued', [_user(_REASON_PREFIX + ' typed by the user')], 0),
            ('no request', [], 0),
        ):
            with self.subTest(name=name):
                self.write(*events)
                turn, already_requested = _utility['get_latest_turn'](self.path, _REASON_PREFIX)
                self.assertEqual(turn, _get_turn_events(self.path)[-1])
                self.assertEqual(_utility['count_requests_by_hook'](turn, _REASON_PREFIX), expected)
                self.assertIs(already_requested, expected > 0)

    def test_get_request(self):
        for name, events, expected in (
            ('request', [('user.message', {'content': 'do it'}), ('assistant.message', {'content': 'done'})], 'do it'),
            ('not a string', [('user.message', {'content': None})], None),
            ('before the first request', [('assistant.message', {'content': 'hello'})], None),
            ('empty', [], None),
        ):
            with self.subTest(name=name):
                self.assertEqual(_utility['get_request'](events), expected)

    def test_get_request_texts_excludes_hook_feedback(self):
        def hook_end(reason, hook_type='agentStop', decision='block'):
            return ('hook.end', {'hookType': hook_type, 'output': {'decision': decision, 'reason': reason}})

        events = [
            ('user.message', {'content': 'request'}),
            ('user.message', {'content': None, 'delivery': 'queued'}),
            hook_end('Hook feedback'),
            ('user.message', {'content': 'Hook feedback', 'delivery': 'queued'}),
            ('user.message', {'content': 'interrupt', 'delivery': 'queued'}),
            hook_end('Other feedback', hook_type='postToolUse'),
            ('user.message', {'content': 'Other feedback', 'delivery': 'queued'}),
            hook_end('Allowed feedback', decision='allow'),
            ('user.message', {'content': 'Allowed feedback', 'delivery': 'queued'}),
            ('hook.end', {'hookType': 'agentStop', 'output': 'Hook feedback'}),
        ]
        self.assertEqual(
            _utility['get_request_texts'](events),
            ['request', 'interrupt', 'Other feedback', 'Allowed feedback'],
        )

    def test_get_last_assistant_message_skips_blank_and_non_string_content(self):
        events = [
            ('assistant.message', {'content': 'first'}),
            ('assistant.message', {'content': 'last'}),
            ('assistant.message', {'content': ' \n'}),
            ('assistant.message', {'content': None}),
            ('user.message', {'content': 'queued', 'delivery': 'queued'}),
        ]
        self.assertEqual(_utility['get_last_assistant_message'](events), 'last')
        self.assertIsNone(_utility['get_last_assistant_message']([('user.message', {'content': 'request'})]))

    def test_tool_calls_in_completion_order(self):
        events = [
            ('tool.execution_start', {'toolCallId': 'a', 'toolName': 'functions.bash', 'arguments': '{"command":"x"}'}),
            ('tool.execution_start', {'toolCallId': 'b', 'toolName': 'edit', 'arguments': {'path': 'p'}}),
            ('tool.execution_start', {'toolCallId': 'c', 'toolName': 'view'}),
            ('tool.execution_start', {'toolCallId': 1, 'toolName': 'view'}),
            ('tool.execution_complete', {'toolCallId': 'b', 'success': True}),
            ('tool.execution_complete', {'toolCallId': 'unknown', 'success': True}),
            ('tool.execution_complete', {'toolCallId': 'a', 'success': False}),
        ]
        # 完了していない呼び出しは最後に加え、toolCallIdが文字列でない開始は対応づけられないため除く。
        self.assertEqual(_utility['get_tool_calls'](events), [
            ('edit', {'path': 'p'}, {'toolCallId': 'b', 'success': True}),
            ('bash', '{"command":"x"}', {'toolCallId': 'a', 'success': False}),
            ('view', None, None),
        ])

    def test_shell_exit_code(self):
        for completion, expected in (
            ({'shellExecution': {'exitCode': 0}}, 0),
            ({'shellExecution': {'exitCode': 2}}, 2),
            ({'shellExecution': {'exitCode': True}}, None),
            ({'shellExecution': {'exitCode': '0'}}, None),
            ({'shellExecution': None}, None),
            ({}, None),
            (None, None),
        ):
            with self.subTest(completion=completion):
                self.assertEqual(_utility['get_shell_exit_code'](completion), expected)


if __name__ == '__main__':
    _run_tests()
