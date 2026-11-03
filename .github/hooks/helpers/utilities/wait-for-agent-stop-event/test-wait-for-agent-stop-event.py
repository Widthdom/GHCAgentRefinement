import json
import os
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

script_directory = os.path.dirname(os.path.abspath(__file__))
utility = runpy.run_path(os.path.join(script_directory, 'wait-for-agent-stop-event.py'))
wait_for_agent_stop_event = utility['wait_for_agent_stop_event']

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


def hook_start(hook_type, timestamp):
    return json.dumps({'type': 'hook.start', 'data': {'hookType': hook_type, 'input': {'timestamp': timestamp}}})


class WaitForAgentStopEventTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-wait-for-agent-stop-event-test-')
        self.addCleanup(temporary.cleanup)
        self.path = os.path.join(temporary.name, 'events.jsonl')

    def write(self, *lines, mode='w'):
        with open(self.path, mode, encoding='utf-8', newline='') as file:
            file.write(''.join(f'{line}\n' for line in lines))

    def wait(self, timestamp, sleep=None, monotonic=None):
        sleep = sleep or Mock()
        with (
            patch.object(utility['time'], 'sleep', sleep),
            patch.object(utility['time'], 'monotonic', monotonic or Mock(side_effect=range(1000))),
        ):
            wait_for_agent_stop_event(self.path, timestamp, timeout_seconds=5.0)
        return sleep

    def test_returns_without_waiting_when_recorded(self):
        # 不完全な行・他のhook・timestampの異なるagentStopの後でも、一致するものを見つける。
        self.write(
            '{"type":"assistant.message"', hook_start('preToolUse', 100), hook_start('agentStop', 99),
            json.dumps({'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': 'unexpected'}}),
            hook_start('agentStop', 100),
        )
        self.wait(100).assert_not_called()

    def test_waits_until_recorded(self):
        self.write(hook_start('agentStop', 99))
        sleep = Mock(side_effect=lambda _: self.write(hook_start('agentStop', 100), mode='a'))
        self.wait(100, sleep).assert_called_once_with(0.05)

    def test_stops_at_deadline(self):
        self.write(hook_start('agentStop', 99))
        # 開始時刻0で期限は5。確認ごとに時刻を2ずつ進めると、0・2・4で待ち、6で期限を過ぎる。
        sleep = self.wait(100, monotonic=Mock(side_effect=[0, 0, 2, 4, 6]))
        self.assertEqual(sleep.call_count, 3)

    def test_skips_non_numeric_timestamp(self):
        # timestampを照合できなければファイルを読まずに戻る。
        for timestamp in (None, '100', True):
            with self.subTest(timestamp=timestamp):
                self.wait(timestamp).assert_not_called()

    def test_rejects_invalid_events(self):
        for line in ('[1]', json.dumps({'type': 'hook.start', 'data': []})):
            with self.subTest(line=line):
                self.write(line)
                with self.assertRaises(ValueError):
                    self.wait(100)


if __name__ == '__main__':
    run_tests()
