import os
import runpy
import sys
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_unwrap_tool_args = runpy.run_path(os.path.join(_script_directory, 'unwrap-tool-args.py'))['unwrap_tool_args']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class UnwrapToolArgsTests(unittest.TestCase):
    def test_unwraps_json_strings(self):
        for raw, expected in (
            ('{"path":"a"}', {'path': 'a'}),
            ('[1, "b"]', [1, 'b']),
            ('"text"', 'text'),
            ('null', None),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(_unwrap_tool_args(raw), expected)

    def test_returns_non_json_strings_as_is(self):
        # 生のパッチ文字列など、JSONとして解析できない文字列はそのまま返す。
        for raw in ('*** Begin Patch', '', '{"path":'):
            with self.subTest(raw=raw):
                self.assertEqual(_unwrap_tool_args(raw), raw)

    def test_returns_non_strings_as_is(self):
        value = {'path': 'a'}
        self.assertIs(_unwrap_tool_args(value), value)
        for raw in (None, 1, ['a']):
            with self.subTest(raw=raw):
                self.assertEqual(_unwrap_tool_args(raw), raw)


if __name__ == '__main__':
    run_tests()
