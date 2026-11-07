import os
import re
import runpy
import sys
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_build_regex_matchers = runpy.run_path(os.path.join(_script_directory, 'build-regex-matchers.py'))['build_regex_matchers']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class BuildRegexMatchersTests(unittest.TestCase):
    def test_builds_matchers_in_order(self):
        matchers = _build_regex_matchers([r'^a', r'b\d'], 'key')
        self.assertEqual([matcher.pattern for matcher in matchers], [r'^a', r'b\d'])
        self.assertIsNotNone(matchers[1].search('xb1'))
        self.assertEqual(_build_regex_matchers([], 'key'), [])

    def test_flags(self):
        self.assertIsNone(_build_regex_matchers([r'^b$'], 'key')[0].search('a\nb'))
        self.assertIsNotNone(_build_regex_matchers([r'^b$'], 'key', re.MULTILINE)[0].search('a\nb'))

    def test_invalid_pattern_names_the_key(self):
        with self.assertRaises(ValueError) as context:
            _build_regex_matchers([r'a', r'('], 'testFilePatterns')
        self.assertIn('invalid regular expression in testFilePatterns:', str(context.exception))
        # 元の例外は連鎖させず、定義の読み込みエラーとして示す。
        self.assertIsNone(context.exception.__cause__)


if __name__ == '__main__':
    run_tests()
