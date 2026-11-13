import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_is_non_blank_string_list = runpy.run_path(
    os.path.join(_script_directory, 'check-string-list.py')
)['is_non_blank_string_list']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class CheckStringListTests(unittest.TestCase):
    def test_accepts_lists_of_non_blank_strings(self):
        for value in (['a'], ['a', ' b '], ['日本語']):
            with self.subTest(value=value):
                self.assertIs(_is_non_blank_string_list(value), True)
                self.assertIs(_is_non_blank_string_list(value, allow_empty_list=True), True)

    def test_empty_list_depends_on_allow_empty_list(self):
        self.assertIs(_is_non_blank_string_list([]), False)
        self.assertIs(_is_non_blank_string_list([], allow_empty_list=True), True)

    def test_rejects_other_values(self):
        # 空文字列や空白だけの要素は、allow_empty_listの指定によらず認めない。
        for value in (None, 'a', ('a',), {'a': 'b'}, [''], [' \t\n'], ['a', ''], ['a', 1], ['a', None], [['a']]):
            with self.subTest(value=value):
                self.assertIs(_is_non_blank_string_list(value), False)
                self.assertIs(_is_non_blank_string_list(value, allow_empty_list=True), False)


if __name__ == '__main__':
    _run_tests()
