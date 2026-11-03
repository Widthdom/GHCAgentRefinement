import os
import runpy
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'hook-activity-state.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class HookActivityStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-hook-activity-state-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.temp_directory = temporary.name
        self.session_id = str(uuid.uuid4())

    def test_state_path(self):
        session_id = uuid.uuid4()
        self.assertEqual(
            _utility['_get_state_path'](f' {str(session_id).upper()} '),
            os.path.join(self.temp_directory, 'ghc-agent-refinement', 'hook-states', 'hook-activity',
                         f'{session_id}.sqlite3'),
        )
        for invalid in (None, '', '  ', 'not-a-uuid'):
            with self.subTest(session_id=invalid), self.assertRaises(ValueError):
                _utility['_get_state_path'](invalid)

    def test_missing_state_has_no_fired_hooks(self):
        self.assertEqual(_utility['get_fired_hooks'](self.session_id, 0, ()), [])

    def test_fired_hooks_since_time_excluding_events(self):
        record = _utility['record_hook_run']
        record(self.session_id, 'preToolUse', 'old-hook', 99, True)
        record(self.session_id, 'preToolUse', 'quiet-hook', 100, False)
        record(self.session_id, 'preToolUse', 'z-hook', 100, True)
        record(self.session_id, 'postToolUse', 'a-hook', 101, True)
        record(self.session_id, 'postToolUse', 'a-hook', 102, True)
        record(self.session_id, 'agentStop', 'stop-hook', 103, True)
        record(str(uuid.uuid4()), 'preToolUse', 'other-session-hook', 100, True)
        get_fired_hooks = _utility['get_fired_hooks']
        # 機能したhookだけを、重複なしの名前順で返す。
        self.assertEqual(get_fired_hooks(self.session_id, 100, ()), ['a-hook', 'stop-hook', 'z-hook'])
        self.assertEqual(get_fired_hooks(self.session_id, 100, ['agentStop']), ['a-hook', 'z-hook'])
        self.assertEqual(get_fired_hooks(self.session_id, 100, ('agentStop', 'postToolUse')), ['z-hook'])
        self.assertEqual(get_fired_hooks(self.session_id, 104, ()), [])


if __name__ == '__main__':
    run_tests()
