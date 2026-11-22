import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'parse_tool_calls.py'))
_unwrap_tool_args = _utility['unwrap_tool_args']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook_test_output.py'))['run_tests']

_EDIT_TOOL_NAMES = ('edit', 'create', 'insert', 'str_replace_editor', 'apply_patch')
_SHELL_TOOL_NAMES = ('bash', 'powershell')


class ToolKindTests(unittest.TestCase):
    def test_classifies_tools_with_or_without_prefix(self):
        for names, matching, other in (
            (_EDIT_TOOL_NAMES, 'is_edit_tool', 'is_shell_tool'),
            (_SHELL_TOOL_NAMES, 'is_shell_tool', 'is_edit_tool'),
        ):
            for name in names:
                for tool_name in (name, f'functions.{name}'):
                    with self.subTest(tool_name=tool_name):
                        self.assertTrue(_utility[matching](tool_name))
                        self.assertFalse(_utility[other](tool_name))
        self.assertEqual(_utility['EDIT_TOOL_NAMES'], frozenset(_EDIT_TOOL_NAMES))
        self.assertEqual(_utility['SHELL_TOOL_NAMES'], frozenset(_SHELL_TOOL_NAMES))

    def test_rejects_other_tools(self):
        # 閲覧・検索のツール、大文字小文字や接頭辞が違う名前は、どちらの種類でもない。
        for tool_name in ('view', 'grep', 'Edit', 'BASH', '', 'functions.', 'other.edit', 'functions.functions.bash'):
            with self.subTest(tool_name=tool_name):
                self.assertFalse(_utility['is_edit_tool'](tool_name))
                self.assertFalse(_utility['is_shell_tool'](tool_name))

    def test_get_tool_name_removes_one_prefix(self):
        for tool_name, expected in (
            ('functions.bash', 'bash'), ('view', 'view'), ('functions.functions.bash', 'functions.bash'), ('', ''),
        ):
            with self.subTest(tool_name=tool_name):
                self.assertEqual(_utility['get_tool_name'](tool_name), expected)


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

    def test_parse_tool_call(self):
        self.assertEqual(_utility['parse_tool_call']('functions.edit', '{"path":"a"}'), ('edit', {'path': 'a'}))
        self.assertEqual(_utility['parse_tool_call']('apply_patch', '*** Begin Patch'), ('apply_patch', '*** Begin Patch'))


if __name__ == '__main__':
    _run_tests()
