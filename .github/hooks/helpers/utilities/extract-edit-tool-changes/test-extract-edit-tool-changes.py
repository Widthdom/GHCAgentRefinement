import json
import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'extract-edit-tool-changes.py'))
_extract = _utility['extract_edit_tool_changes']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


def _change(path, written='', removed='', **values):
    return {
        'path': path, 'written': written, 'removed': removed, 'deleted': False, 'moved_to': None,
        'full_content': None, 'replacement': None, 'patch_lines': None, **values,
    }


_PATCH = '\n'.join([
    '*** Begin Patch',
    '*** Update File: src/a.py',
    '@@ def f():',
    ' context',
    '-old',
    '+new',
    '*** Add File: src/b.py',
    '+line1',
    '+line2',
    '*** Delete File: src/c.py',
    '*** End Patch',
])


class ExtractEditToolChangesTests(unittest.TestCase):
    def test_replace_tools(self):
        for tool_name, arguments in (
            ('edit', {'path': 'a.py', 'old_str': 'x = 1', 'new_str': 'x = 2'}),
            ('functions.edit', {'path': 'a.py', 'old_str': 'x = 1', 'new_str': 'x = 2'}),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'a.py', 'old_str': 'x = 1', 'new_str': 'x = 2'}),
            ('edit', {'path': 'a.py', 'old_string': 'x = 1', 'new_string': 'x = 2'}),
        ):
            with self.subTest(tool_name=tool_name, arguments=arguments):
                self.assertEqual(
                    _extract(tool_name, arguments), [_change('a.py', 'x = 2', 'x = 1', replacement=('x = 1', 'x = 2'))],
                )

    def test_insert_and_create(self):
        self.assertEqual(
            _extract('insert', {'path': 'a.py', 'insert_line': 3, 'new_str': 'y = 1'}), [_change('a.py', 'y = 1')],
        )
        # 作成ではファイル全体の内容も返す。
        for tool_name, arguments in (
            ('create', {'path': 'b.py', 'file_text': 'z = 1'}),
            ('create', {'path': 'b.py', 'content': 'z = 1'}),
            ('str_replace_editor', {'command': 'create', 'path': 'b.py', 'file_text': 'z = 1'}),
        ):
            with self.subTest(tool_name=tool_name, arguments=arguments):
                self.assertEqual(_extract(tool_name, arguments), [_change('b.py', 'z = 1', full_content='z = 1')])

    def test_replacement_requires_old_text(self):
        # 置換前の内容が空なら、置換を適用できないためNoneにする。新しい内容は空でもよい。
        self.assertIsNone(_extract('edit', {'path': 'a.py', 'old_str': '', 'new_str': 'x'})[0]['replacement'])
        self.assertEqual(_extract('edit', {'path': 'a.py', 'old_str': 'x', 'new_str': ''})[0]['replacement'], ('x', ''))

    def test_raw_arguments(self):
        # JSON文字列の引数と、生のパッチ文字列を受け付ける。
        self.assertEqual(
            _extract('edit', json.dumps({'path': 'a.py', 'old_str': 'a', 'new_str': 'b'})),
            [_change('a.py', 'b', 'a', replacement=('a', 'b'))],
        )
        self.assertEqual([item['path'] for item in _extract('apply_patch', _PATCH)], ['src/a.py', 'src/b.py'])

    def test_patch(self):
        a_lines = [(' ', 'context'), ('-', 'old'), ('+', 'new')]
        b_lines = [('+', 'line1'), ('+', 'line2')]
        expected = [
            _change('src/a.py', 'new', 'old', patch_lines=a_lines),
            _change('src/b.py', 'line1\nline2', patch_lines=b_lines),
        ]
        for arguments in (_PATCH, {'input': _PATCH}, [_PATCH]):
            with self.subTest(arguments=type(arguments).__name__):
                self.assertEqual(_extract('apply_patch', arguments), expected)
        # ファイルの削除は、指定したときだけ含める。
        self.assertEqual(
            _extract('apply_patch', _PATCH, include_deleted=True),
            [*expected, _change('src/c.py', deleted=True, patch_lines=[])],
        )

    def test_patch_move(self):
        # `*** Move to:` の移動先を、移動元のファイルの変更に記録する。行の内容には含めない。
        patch = '\n'.join([
            '*** Begin Patch',
            '*** Update File: src/old.py',
            '*** Move to: src/new.py',
            '@@',
            '-a',
            '+b',
            '*** End Patch',
        ])
        self.assertEqual(
            _extract('apply_patch', patch),
            [_change('src/old.py', 'b', 'a', moved_to='src/new.py', patch_lines=[('-', 'a'), ('+', 'b')])],
        )

    def test_not_written(self):
        for tool_name, arguments in (
            ('bash', {'command': 'echo hi > a.py'}),
            ('view', {'path': 'a.py'}),
            ('str_replace_editor', {'command': 'view', 'path': 'a.py'}),
            ('str_replace_editor', {'command': 'undo_edit', 'path': 'a.py'}),
            ('edit', {'old_str': 'a', 'new_str': 'b'}),
            ('edit', {'path': '  ', 'new_str': 'b'}),
            ('edit', ['not', 'an', 'object']),
            ('edit', 'not json'),
            ('apply_patch', 'no patch headers'),
        ):
            with self.subTest(tool_name=tool_name, arguments=arguments):
                self.assertEqual(_extract(tool_name, arguments), [])

    def test_written_excludes_path_and_command(self):
        # 入れ子の文字列も書き込む内容とし、パス・コマンド・置換前の内容は除く。
        result = _extract('edit', {'path': 'a.py', 'command': 'x', 'old_str': 'o', 'edits': [{'new_str': 'n1'}, 'n2']})
        self.assertEqual((result[0]['written'], result[0]['removed']), ('n1\nn2', 'o'))


if __name__ == '__main__':
    _run_tests()
