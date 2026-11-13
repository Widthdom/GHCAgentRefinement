import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_truncate_text = runpy.run_path(os.path.join(_script_directory, 'truncate-text.py'))['truncate_text']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class TruncateTextTests(unittest.TestCase):
    def test_keeps_text_within_the_kept_length(self):
        for text in ('', 'abc', 'abcde'):
            with self.subTest(text=text):
                self.assertEqual(_truncate_text(text, 5), text)

    def test_truncates_text_over_the_kept_length(self):
        # `...` は残す文字数の外に付く。
        self.assertEqual(_truncate_text('abcdef', 5), 'abcde...')
        # 長さは文字数で数える。
        self.assertEqual(_truncate_text('あいうえおか', 5), 'あいうえお...')
        self.assertEqual(_truncate_text('a', 0), '...')


if __name__ == '__main__':
    _run_tests()
