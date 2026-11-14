import json
import os
import runpy
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'edit-tool-changes.py'))
_get_edited_paths = _utility['get_edited_paths']
_extract = _utility['extract_edit_tool_changes']
_simulate = _utility['simulate_edit_tool_changes']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

# 追加・移動を伴う更新・削除を含むパッチ。更新は a.txt の内容（`one\ntwo\n`）に適用できる。
_PATCH = '\n'.join([
    '*** Begin Patch',
    '*** Add File: added.txt',
    '+x',
    '+y',
    '*** Update File: a.txt  ',
    '*** Move to: moved.txt',
    '@@',
    ' one',
    '-two',
    '+2',
    '*** End of File',
    '*** Delete File: deleted.txt',
    '*** End Patch',
])


def _change(path, written='', removed='', **values):
    return {
        'path': path, 'written': written, 'removed': removed, 'deleted': False, 'moved_to': None,
        'full_content': None, 'replacement': None, 'patch_lines': None, **values,
    }


class _TemporaryCwdTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-edit-tool-changes-test-')
        self.addCleanup(temporary.cleanup)
        self.cwd = temporary.name
        self.write('a.txt', 'one\ntwo\n')

    def write(self, name, content):
        with open(os.path.join(self.cwd, name), 'w', encoding='utf-8', newline='') as file:
            file.write(content)

    def simulate(self, tool_name, tool_args):
        return [
            (os.path.relpath(path, self.cwd).replace(os.sep, '/'), before, after)
            for path, before, after in _simulate(tool_name, tool_args, self.cwd)
        ]


class ToolCallParsingTests(_TemporaryCwdTestCase):
    # 3つの関数に共通する、ツール呼び出しの解釈。
    def __get_all(self, tool_name, tool_args):
        return (
            _get_edited_paths(tool_name, tool_args),
            [change['path'] for change in _extract(tool_name, tool_args)],
            [path for path, _, _ in self.simulate(tool_name, tool_args)],
        )

    def test_path_argument(self):
        # `create` はパスを `file_path` で渡す。`path` があればそちらを優先し、空白だけの値は無視する。
        get_path_argument = _utility['get_path_argument']
        self.assertEqual(get_path_argument({'path': 'a.txt', 'file_path': 'b.txt'}), 'a.txt')
        self.assertEqual(get_path_argument({'path': ' ', 'file_path': 'b.txt'}), 'b.txt')
        for tool_args in ({'file_path': ' '}, {'path': 1}, {}, 'a.txt'):
            with self.subTest(tool_args=tool_args):
                self.assertIsNone(get_path_argument(tool_args))

    def test_accepts_prefixed_name_and_wrapped_arguments(self):
        tool_args = {'path': 'a.txt', 'old_str': 'two', 'new_str': '2'}
        for tool_name, arguments in (
            ('edit', tool_args),
            ('functions.edit', tool_args),
            ('edit', json.dumps(tool_args)),
            ('edit', {'file_path': 'a.txt', 'old_string': 'two', 'new_string': '2'}),
        ):
            with self.subTest(tool_name=tool_name, arguments=arguments):
                self.assertEqual(self.__get_all(tool_name, arguments), (['a.txt'], ['a.txt'], ['a.txt']))

    def test_ignores_non_edit_tools_and_invalid_arguments(self):
        for tool_name, tool_args in (
            ('bash', {'command': 'echo hi > a.txt', 'path': 'a.txt'}),
            ('view', {'path': 'a.txt'}),
            ('str_replace_editor', {'command': 'view', 'path': 'a.txt'}),
            ('edit', {'old_str': 'one', 'new_str': 'x'}),
            ('edit', {'path': ' ', 'old_str': 'one', 'new_str': 'x'}),
            ('edit', ['not', 'an', 'object']),
            ('edit', 'not json'),
            ('apply_patch', 'no patch headers'),
            # 行頭が見出しでない行と、パスのない見出しは無視する。
            ('apply_patch', '+*** Add File: x.txt\n*** Add File:\n'),
        ):
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                self.assertEqual(self.__get_all(tool_name, tool_args), ([], [], []))

    def test_undo_edit_changes_path_without_content(self):
        # undo_editは対象のファイルを書き換えるが、書き込む内容を引数に持たない。
        tool_args = {'command': 'undo_edit', 'path': 'a.txt'}
        self.assertEqual(self.__get_all('str_replace_editor', tool_args), (['a.txt'], [], []))

    def test_patch_anywhere_in_arguments(self):
        # パッチの文字列は、引数のどこにあってもよい。行末の空白はパスに含めない。
        for tool_args in (_PATCH, {'input': _PATCH}, [_PATCH], {'outer': {'input': _PATCH}}, json.dumps({'input': _PATCH})):
            with self.subTest(tool_args=tool_args):
                self.assertEqual(self.__get_all('apply_patch', tool_args), (
                    ['added.txt', 'a.txt', 'moved.txt', 'deleted.txt'], ['added.txt', 'a.txt'], ['added.txt', 'moved.txt'],
                ))

    def test_ignores_lines_after_end_of_patch(self):
        patch = '*** Begin Patch\n*** Add File: added.txt\n+x\n*** End Patch\n+y\n'
        self.assertEqual(_extract('apply_patch', patch)[0]['written'], 'x')


class GetEditedPathsTests(unittest.TestCase):
    def test_returns_path_for_each_edit_tool(self):
        for tool_name in ('edit', 'create', 'insert', 'str_replace_editor'):
            with self.subTest(tool_name=tool_name):
                self.assertEqual(_get_edited_paths(tool_name, {'path': 'a.txt', 'command': 'str_replace'}), ['a.txt'])

    def test_filters_patch_operations(self):
        for operations, expected in (
            ({'Update File'}, ['a.txt']),
            ({'Add File', 'Update File'}, ['added.txt', 'a.txt']),
            ({'Add File', 'Update File', 'Move to'}, ['added.txt', 'a.txt', 'moved.txt']),
            (set(), []),
        ):
            with self.subTest(operations=sorted(operations)):
                self.assertEqual(_get_edited_paths('apply_patch', _PATCH, operations), expected)


class ExtractEditToolChangesTests(unittest.TestCase):
    def test_replace(self):
        for tool_name, arguments in (
            ('edit', {'path': 'a.py', 'old_str': 'x = 1', 'new_str': 'x = 2'}),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'a.py', 'old_str': 'x = 1', 'new_str': 'x = 2'}),
        ):
            with self.subTest(tool_name=tool_name):
                self.assertEqual(
                    _extract(tool_name, arguments), [_change('a.py', 'x = 2', 'x = 1', replacement=('x = 1', 'x = 2'))],
                )

    def test_insert_and_create(self):
        self.assertEqual(
            _extract('insert', {'path': 'a.py', 'insert_line': 3, 'new_str': 'y = 1'}), [_change('a.py', 'y = 1')],
        )
        # 作成ではファイル全体の内容も返す。`file_path` はパスなので、書き込む内容に含めない。
        for tool_name, arguments in (
            ('create', {'path': 'b.py', 'file_text': 'z = 1'}),
            ('create', {'path': 'b.py', 'content': 'z = 1'}),
            ('create', {'file_path': 'b.py', 'file_text': 'z = 1'}),
            ('str_replace_editor', {'command': 'create', 'path': 'b.py', 'file_text': 'z = 1'}),
        ):
            with self.subTest(tool_name=tool_name, arguments=arguments):
                self.assertEqual(_extract(tool_name, arguments), [_change('b.py', 'z = 1', full_content='z = 1')])

    def test_replacement_requires_old_text(self):
        # 置換前の内容が空なら、置換を適用できないためNoneにする。新しい内容は空でもよい。
        self.assertIsNone(_extract('edit', {'path': 'a.py', 'old_str': '', 'new_str': 'x'})[0]['replacement'])
        self.assertEqual(_extract('edit', {'path': 'a.py', 'old_str': 'x', 'new_str': ''})[0]['replacement'], ('x', ''))

    def test_written_excludes_path_and_command(self):
        # 入れ子の文字列も書き込む内容とし、パス・コマンド・置換前の内容は除く。
        result = _extract('edit', {'path': 'a.py', 'command': 'x', 'old_str': 'o', 'edits': [{'new_str': 'n1'}, 'n2']})
        self.assertEqual((result[0]['written'], result[0]['removed']), ('n1\nn2', 'o'))

    def test_patch(self):
        # 移動先は移動元のファイルの変更に記録する。空行は文脈行として本文の行に含める。
        patch = _PATCH.replace(' one', '')
        added = _change('added.txt', 'x\ny', patch_lines=[('+', 'x'), ('+', 'y')])
        updated = _change(
            'a.txt', '2', 'two', moved_to='moved.txt', patch_lines=[(' ', ''), ('-', 'two'), ('+', '2')],
        )
        self.assertEqual(_extract('apply_patch', patch), [added, updated])
        # ファイルの削除は、指定したときだけ含める。
        self.assertEqual(
            _extract('apply_patch', patch, include_deleted=True),
            [added, updated, _change('deleted.txt', deleted=True, patch_lines=[])],
        )


class SimulateEditToolChangesTests(_TemporaryCwdTestCase):
    def test_create(self):
        for tool_name, tool_args in (
            ('create', {'path': 'new.txt', 'file_text': 'x\n'}),
            ('create', {'path': 'new.txt', 'content': 'x\n'}),
            ('str_replace_editor', {'command': 'create', 'path': 'new.txt', 'file_text': 'x\n'}),
        ):
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                self.assertEqual(self.simulate(tool_name, tool_args), [('new.txt', None, 'x\n')])
        self.assertEqual(self.simulate('create', {'path': 'a.txt', 'file_text': 'x\n'}), [('a.txt', 'one\ntwo\n', 'x\n')])

    def test_replace_and_insert(self):
        for tool_name, tool_args, expected in (
            ('edit', {'path': 'a.txt', 'old_str': 'two', 'new_str': '2'}, 'one\n2\n'),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'a.txt', 'old_str': 'one\n'}, 'two\n'),
            ('insert', {'path': 'a.txt', 'insert_line': 0, 'new_str': 'zero'}, 'zero\none\ntwo\n'),
            ('str_replace_editor', {'command': 'insert', 'path': 'a.txt', 'insert_line': 2, 'new_str': 'three\n'},
             'one\ntwo\nthree\n'),
        ):
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                self.assertEqual(self.simulate(tool_name, tool_args), [('a.txt', 'one\ntwo\n', expected)])

    def test_insert_after_last_line_without_newline(self):
        self.write('b.txt', 'one')
        self.assertEqual(
            self.simulate('insert', {'path': 'b.txt', 'insert_line': 1, 'new_str': 'two'}), [('b.txt', 'one', 'one\ntwo\n')],
        )

    def test_apply_patch(self):
        # 文脈行は末尾の空白の違いを許容して照合し、パッチ側の行で置き換える。移動では移動先を書き込み先とする。
        patch = _PATCH.replace(' one', ' one  ')
        self.assertEqual(
            self.simulate('apply_patch', patch), [('added.txt', None, 'x\ny\n'), ('moved.txt', 'one\ntwo\n', 'one  \n2\n')],
        )

    def test_skips_undetermined_edits(self):
        with open(os.path.join(self.cwd, 'binary.txt'), 'wb') as file:
            file.write(b'\xff\xfe')
        for tool_name, tool_args in (
            ('edit', {'path': 'a.txt', 'old_str': 'missing', 'new_str': 'x'}),
            ('edit', {'path': 'a.txt', 'old_str': '', 'new_str': 'x'}),
            ('edit', {'path': 'missing.txt', 'old_str': 'one', 'new_str': 'x'}),
            ('edit', {'path': 'binary.txt', 'old_str': 'x', 'new_str': 'y'}),
            ('insert', {'path': 'a.txt', 'insert_line': 3, 'new_str': 'x'}),
            ('insert', {'path': 'a.txt', 'insert_line': True, 'new_str': 'x'}),
            ('create', {'path': 'new.txt'}),
            ('apply_patch', '*** Begin Patch\n*** Update File: a.txt\n@@\n-missing\n+x\n*** End Patch'),
            ('apply_patch', '*** Begin Patch\n*** Update File: missing.txt\n@@\n-x\n+y\n*** End Patch'),
        ):
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                self.assertEqual(self.simulate(tool_name, tool_args), [])


if __name__ == '__main__':
    _run_tests()
