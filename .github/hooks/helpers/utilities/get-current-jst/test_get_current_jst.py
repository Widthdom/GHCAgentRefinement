import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

_script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_directory)
from get_current_jst import get_current_jst as _get_current_jst

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests


class GetCurrentJstTests(unittest.TestCase):
    def test_current_time_in_jst(self):
        jst = timezone(timedelta(hours=9))
        before = datetime.now(jst).replace(microsecond=0)
        current_jst = _get_current_jst()
        after = datetime.now(jst)
        self.assertRegex(current_jst, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} JST$')
        # 夏時間のないUTC+9で、呼び出した時点の時刻を表すこと。
        parsed = datetime.strptime(re.sub(' JST$', '', current_jst), '%Y-%m-%d %H:%M:%S').replace(tzinfo=jst)
        self.assertLessEqual(before, parsed)
        self.assertLessEqual(parsed, after)


if __name__ == '__main__':
    _run_tests()
