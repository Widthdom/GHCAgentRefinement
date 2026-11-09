import os
import runpy
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_simulate_edit_tool_changes = runpy.run_path(
    os.path.join(_script_directory, 'simulate-edit-tool-changes.py'),
)['simulate_edit_tool_changes']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class SimulateEditToolChangesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-simulate-edit-tool-changes-test-')
        self.addCleanup(temporary.cleanup)
        self.__cwd = temporary.name
        self.__write('a.txt', 'one\ntwo\n')

    def __write(self, name, content):
        with open(os.path.join(self.__cwd, name), 'w', encoding='utf-8', newline='') as file:
            file.write(content)

    def __get(self, tool_name, tool_args):
        return [
            (os.path.relpath(path, self.__cwd).replace(os.sep, '/'), before, after)
            for path, before, after in _simulate_edit_tool_changes(tool_name, tool_args, self.__cwd)
        ]

    def test_create(self):
        for tool_name, tool_args in (
            ('create', {'path': 'new.txt', 'file_text': 'x\n'}),
            ('create', {'path': 'new.txt', 'content': 'x\n'}),
            ('str_replace_editor', {'command': 'create', 'path': 'new.txt', 'file_text': 'x\n'}),
        ):
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                self.assertEqual(self.__get(tool_name, tool_args), [('new.txt', None, 'x\n')])
        self.assertEqual(self.__get('create', {'path': 'a.txt', 'file_text': 'x\n'}), [('a.txt', 'one\ntwo\n', 'x\n')])

    def test_replace_and_insert(self):
        for tool_name, tool_args, expected in (
            ('edit', {'path': 'a.txt', 'old_str': 'two', 'new_str': '2'}, 'one\n2\n'),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'a.txt', 'old_str': 'one\n'}, 'two\n'),
            ('insert', {'path': 'a.txt', 'insert_line': 0, 'new_str': 'zero'}, 'zero\none\ntwo\n'),
            ('str_replace_editor', {'command': 'insert', 'path': 'a.txt', 'insert_line': 2, 'new_str': 'three\n'},
             'one\ntwo\nthree\n'),
        ):
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                self.assertEqual(self.__get(tool_name, tool_args), [('a.txt', 'one\ntwo\n', expected)])

    def test_insert_after_last_line_without_newline(self):
        self.__write('b.txt', 'one')
        self.assertEqual(
            self.__get('insert', {'path': 'b.txt', 'insert_line': 1, 'new_str': 'two'}), [('b.txt', 'one', 'one\ntwo\n')],
        )

    def test_apply_patch(self):
        patch = '\n'.join([
            '*** Begin Patch',
            '*** Add File: added.txt',
            '+x',
            '*** Update File: a.txt',
            '*** Move to: moved.txt',
            '@@',
            ' one  ',
            '-two',
            '+2',
            '*** End of File',
            '*** Delete File: deleted.txt',
            '*** End Patch',
        ])
        # 文脈行は末尾の空白の違いを許容して照合し、パッチ側の行で置き換える。
        expected = [('added.txt', None, 'x\n'), ('moved.txt', 'one\ntwo\n', 'one  \n2\n')]
        for tool_args in (patch, {'input': patch}):
            with self.subTest(tool_args=type(tool_args).__name__):
                self.assertEqual(self.__get('apply_patch', tool_args), expected)

    def test_skips_undetermined_and_non_write_edits(self):
        with open(os.path.join(self.__cwd, 'binary.txt'), 'wb') as file:
            file.write(b'\xff\xfe')
        for tool_name, tool_args in (
            ('edit', {'path': 'a.txt', 'old_str': 'missing', 'new_str': 'x'}),
            ('edit', {'path': 'a.txt', 'old_str': '', 'new_str': 'x'}),
            ('edit', {'path': 'missing.txt', 'old_str': 'one', 'new_str': 'x'}),
            ('edit', {'path': 'binary.txt', 'old_str': 'x', 'new_str': 'y'}),
            ('insert', {'path': 'a.txt', 'insert_line': 3, 'new_str': 'x'}),
            ('insert', {'path': 'a.txt', 'insert_line': True, 'new_str': 'x'}),
            ('create', {'path': 'new.txt'}),
            ('str_replace_editor', {'command': 'view', 'path': 'a.txt'}),
            ('str_replace_editor', {'command': 'undo_edit', 'path': 'a.txt'}),
            ('edit', {'path': ' ', 'old_str': 'one', 'new_str': 'x'}),
            ('edit', 'a.txt'),
            ('apply_patch', '*** Begin Patch\n*** Update File: a.txt\n@@\n-missing\n+x\n*** End Patch'),
            ('apply_patch', '*** Begin Patch\n*** Update File: missing.txt\n@@\n-x\n+y\n*** End Patch'),
        ):
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                self.assertEqual(self.__get(tool_name, tool_args), [])


if __name__ == '__main__':
    _run_tests()
