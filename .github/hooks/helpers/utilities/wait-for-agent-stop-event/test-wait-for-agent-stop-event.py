import json
import os
import re
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'wait-for-agent-stop-event.py'))
_wait_for_agent_stop_event = _utility['wait_for_agent_stop_event']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


def _hook_start(hook_type, timestamp):
    return json.dumps({'type': 'hook.start', 'data': {'hookType': hook_type, 'input': {'timestamp': timestamp}}})


class WaitForAgentStopEventTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-wait-for-agent-stop-event-test-')
        self.addCleanup(temporary.cleanup)
        self.__path = os.path.join(temporary.name, 'events.jsonl')

    def __write(self, *lines, mode='w'):
        with open(self.__path, mode, encoding='utf-8', newline='') as file:
            file.write(''.join(f'{line}\n' for line in lines))

    def __wait(self, timestamp, sleep=None, monotonic=None):
        sleep = sleep or Mock()
        with (
            patch.object(_utility['time'], 'sleep', sleep),
            patch.object(_utility['time'], 'monotonic', monotonic or Mock(side_effect=range(1000))),
        ):
            _wait_for_agent_stop_event(self.__path, timestamp, timeout_seconds=5.0)
        return sleep

    def test_returns_without_waiting_when_recorded(self):
        # 不完全な行・hook.start以外・他のhook・形の異なる入力・timestampの異なるagentStopの後でも、一致するものを見つける。
        self.__write(
            '{"type":"assistant.message"', _hook_start('preToolUse', 100), _hook_start('agentStop', 99),
            json.dumps({'type': 'tool.execution_start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': 100}}}),
            json.dumps({'type': 'hook.start'}),
            json.dumps({'type': 'hook.start', 'data': {'hookType': 'agentStop'}}),
            json.dumps({'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': None}}),
            json.dumps({'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': 'unexpected'}}),
            _hook_start('agentStop', 100),
        )
        self.__wait(100).assert_not_called()

    def test_waits_until_recorded(self):
        self.__write(_hook_start('agentStop', 99))
        sleep = Mock(side_effect=lambda _: self.__write(_hook_start('agentStop', 100), mode='a'))
        self.__wait(100, sleep).assert_called_once_with(0.05)

    def test_stops_at_deadline(self):
        self.__write(_hook_start('agentStop', 99))
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
                self.__write(json.dumps({
                    'type': 'hook.start', 'timestamp': event_time,
                    'data': {'hookType': 'agentStop', 'input': {'timestamp': 'different-format'}},
                }))
                sleep = self.__wait(hook_timestamp, monotonic=Mock(side_effect=[0, 0, 6]))
                self.assertEqual(sleep.call_count, 0 if recorded else 1)

    def test_rejects_invalid_events(self):
        for line, expected in (
            ('[1]', 'Transcript event must be a JSON object at line 2.'),
            (json.dumps({'type': 'hook.start', 'data': []}), 'Event data must be a JSON object at line 2.'),
            (json.dumps({'type': 'hook.start', 'data': None}), 'Event data must be a JSON object at line 2.'),
        ):
            with self.subTest(line=line):
                self.__write('{"type":"user.message"}', line)
                with self.assertRaisesRegex(ValueError, f'^{re.escape(expected)}$'):
                    self.__wait(100)


if __name__ == '__main__':
    _run_tests()
