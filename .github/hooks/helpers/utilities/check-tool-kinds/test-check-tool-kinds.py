import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'check-tool-kinds.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_EDIT_TOOL_NAMES = ('edit', 'create', 'insert', 'str_replace_editor', 'apply_patch')
_SHELL_TOOL_NAMES = ('bash', 'powershell')


class CheckToolKindsTests(unittest.TestCase):
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


if __name__ == '__main__':
    _run_tests()
