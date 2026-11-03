import json
import os
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'wait-for-agent-stop-event.py'))
_wait_for_agent_stop_event = _utility['wait_for_agent_stop_event']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


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
        # 不完全な行・他のhook・timestampの異なるagentStopの後でも、一致するものを見つける。
        self.__write(
            '{"type":"assistant.message"', _hook_start('preToolUse', 100), _hook_start('agentStop', 99),
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

    def test_rejects_invalid_events(self):
        for line in ('[1]', json.dumps({'type': 'hook.start', 'data': []})):
            with self.subTest(line=line):
                self.__write(line)
                with self.assertRaises(ValueError):
                    self.__wait(100)


if __name__ == '__main__':
    run_tests()
