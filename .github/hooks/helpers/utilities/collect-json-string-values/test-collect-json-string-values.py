import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_collect_json_string_values = runpy.run_path(os.path.join(_script_directory, 'collect-json-string-values.py'))['collect_json_string_values']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class CollectJsonStringValuesTests(unittest.TestCase):
    def test_collects_nested_string_values_in_order(self):
        value = {'a': 'x', 'b': ['y', {'c': 'z', 'd': 1}], 'e': None, 'f': True, 'g': 'w'}
        self.assertEqual(list(_collect_json_string_values(value)), ['x', 'y', 'z', 'w'])

    def test_scalars(self):
        for value, expected in (('x', ['x']), ('', ['']), (None, []), (1, []), (False, []), ([], []), ({}, [])):
            with self.subTest(value=value):
                self.assertEqual(list(_collect_json_string_values(value)), expected)

    def test_ignored_keys_apply_at_any_depth(self):
        value = {'path': 'p', 'content': 'c', 'edits': [{'path': 'q', 'new': 'n'}]}
        self.assertEqual(list(_collect_json_string_values(value, frozenset({'path'}))), ['c', 'n'])
        # 無視するキーの名前を持つ文字列の値は、キーではないため集める。
        self.assertEqual(list(_collect_json_string_values(['path'], frozenset({'path'}))), ['path'])


if __name__ == '__main__':
    _run_tests()
