import os
import re
import runpy
import unittest
from datetime import datetime, timedelta, timezone

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility_path = os.path.join(_script_directory, 'get-current-jst.py')

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class GetCurrentJstTests(unittest.TestCase):
    def test_current_time_in_jst(self):
        jst = timezone(timedelta(hours=9))
        before = datetime.now(jst).replace(microsecond=0)
        current_jst = runpy.run_path(_utility_path)['current_jst']
        after = datetime.now(jst)
        self.assertRegex(current_jst, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} JST$')
        # 夏時間のないUTC+9で、読み込んだ時点の時刻を表すこと。
        parsed = datetime.strptime(re.sub(' JST$', '', current_jst), '%Y-%m-%d %H:%M:%S').replace(tzinfo=jst)
        self.assertLessEqual(before, parsed)
        self.assertLessEqual(parsed, after)


if __name__ == '__main__':
    _run_tests()
