import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_module = runpy.run_path(os.path.join(_script_directory, 'get-edited-paths.py'))
_get_edited_paths = _module['get_edited_paths']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_PATCH = '\n'.join([
    '*** Begin Patch',
    '*** Add File: added.txt',
    '+text',
    '*** Update File: old.txt  ',
    '*** Move to: new.txt',
    '@@',
    '-a',
    '+b',
    '*** Delete File: deleted.txt',
    '*** End Patch',
])


class GetEditedPathsTests(unittest.TestCase):
    def test_returns_path_argument(self):
        for tool_name in ('edit', 'create', 'insert', 'str_replace_editor'):
            with self.subTest(tool_name=tool_name):
                self.assertEqual(_get_edited_paths(tool_name, {'path': 'a.txt', 'command': 'str_replace'}), ['a.txt'])

    def test_skips_view_and_invalid_paths(self):
        for tool_name, tool_args in (
            ('str_replace_editor', {'path': 'a.txt', 'command': 'view'}),
            ('edit', {'path': ' '}),
            ('edit', {'path': 1}),
            ('edit', {}),
            ('edit', 'a.txt'),
        ):
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                self.assertEqual(_get_edited_paths(tool_name, tool_args), [])

    def test_returns_patch_paths_in_order(self):
        # 行末の空白は除き、パッチの文字列は引数のどこにあってもよい。
        for tool_args in (_PATCH, {'input': _PATCH}, [_PATCH]):
            with self.subTest(tool_args=type(tool_args).__name__):
                self.assertEqual(
                    _get_edited_paths('apply_patch', tool_args), ['added.txt', 'old.txt', 'new.txt', 'deleted.txt'],
                )

    def test_filters_patch_operations(self):
        for operations, expected in (
            ({'Update File'}, ['old.txt']),
            ({'Add File', 'Update File'}, ['added.txt', 'old.txt']),
            ({'Add File', 'Update File', 'Move to'}, ['added.txt', 'old.txt', 'new.txt']),
            (set(), []),
        ):
            with self.subTest(operations=sorted(operations)):
                self.assertEqual(_get_edited_paths('apply_patch', _PATCH, operations), expected)

    def test_ignores_patch_lines_that_are_not_headers(self):
        self.assertEqual(_get_edited_paths('apply_patch', '+*** Add File: x.txt\n*** Add File:\n'), [])


if __name__ == '__main__':
    _run_tests()
