import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'get_stripped_lines.py'))
_get_stripped_lines = _utility['get_stripped_lines']
_get_stripped_line_at = _utility['get_stripped_line_at']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook_test_output.py'))['run_tests']


class GetStrippedLinesTests(unittest.TestCase):
    def test_strips_each_line(self):
        self.assertEqual(_get_stripped_lines('  a\n\tb  \r\nc'), {'a', 'b', 'c'})

    def test_collapses_lines_that_differ_only_in_indentation(self):
        self.assertEqual(_get_stripped_lines('x = 1\n    x = 1\n'), {'x = 1'})

    def test_keeps_blank_lines_as_empty_strings(self):
        self.assertEqual(_get_stripped_lines('a\n   \nb'), {'a', '', 'b'})

    def test_empty_text(self):
        self.assertEqual(_get_stripped_lines(''), set())


class GetStrippedLineAtTests(unittest.TestCase):
    def test_returns_the_line_containing_the_position(self):
        text = 'first\n    second line\nthird'
        for position, expected in ((0, 'first'), (4, 'first'), (10, 'second line'), (len(text) - 1, 'third')):
            with self.subTest(position=position):
                self.assertEqual(_get_stripped_line_at(text, position), expected)

    def test_position_on_a_newline_returns_the_line_ending_there(self):
        self.assertEqual(_get_stripped_line_at('ab\ncd', 2), 'ab')

    def test_text_without_newlines(self):
        self.assertEqual(_get_stripped_line_at('  only  ', 3), 'only')


if __name__ == '__main__':
    _run_tests()
