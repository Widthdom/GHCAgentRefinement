import os
import re
import runpy
import sys
import unittest
from datetime import datetime, timedelta, timezone

script_directory = os.path.dirname(os.path.abspath(__file__))
utility_path = os.path.join(script_directory, 'get-current-jst.py')

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class GetCurrentJstTests(unittest.TestCase):
    def test_current_time_in_jst(self):
        jst = timezone(timedelta(hours=9))
        before = datetime.now(jst).replace(microsecond=0)
        current_jst = runpy.run_path(utility_path)['current_jst']
        after = datetime.now(jst)
        self.assertRegex(current_jst, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} JST$')
        # 夏時間のないUTC+9で、読み込んだ時点の時刻を表すこと。
        parsed = datetime.strptime(re.sub(' JST$', '', current_jst), '%Y-%m-%d %H:%M:%S').replace(tzinfo=jst)
        self.assertLessEqual(before, parsed)
        self.assertLessEqual(parsed, after)


if __name__ == '__main__':
    run_tests()
