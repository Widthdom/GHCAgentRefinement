import os
import re
import runpy
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_find = runpy.run_path(os.path.join(_script_directory, 'find-introduced-lines-by-regex.py'))['find_introduced_lines_by_regex']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_REGEXES = [('todo', re.compile(r'TODO')), ('print', re.compile(r'print\('))]


def _change(path, written, removed=''):
    return {'path': path, 'written': written, 'removed': removed}


class FindIntroducedLinesByRegexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-find-introduced-lines-test-')
        self.addCleanup(temporary.cleanup)
        self.__cwd = os.path.realpath(temporary.name)

    def __write_file(self, path, content):
        full_path = os.path.join(self.__cwd, *path.split('/'))
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def test_finds_lines_remaining_in_file(self):
        self.__write_file('src/a.py', 'x = 1\n    print(x)  # TODO\n')
        # 一致した位置を含む行全体を、前後の空白を除いて返す。1行が複数の種類に一致しても、先の種類の1件にまとめる。
        self.assertEqual(
            _find([_change('src/a.py', 'x = 1\n    print(x)  # TODO')], self.__cwd, [], _REGEXES),
            [('src/a.py', 'todo', 'print(x)  # TODO')],
        )

    def test_excludes_existing_and_later_removed_lines(self):
        self.__write_file('a.py', 'print(1)\nkept = 1\n')
        changes = [
            # 置換前の内容にある行は、元からあるものとして除く（インデントの違いは問わない）。
            _change('a.py', 'print(1)', removed='    print(1)'),
            # 後の編集で消えた行は、最終的なファイルにないため除く。
            _change('a.py', 'print(2)'),
        ]
        self.assertEqual(_find(changes, self.__cwd, [], _REGEXES), [])

    def test_missing_files_and_excluded_paths(self):
        self.__write_file('docs/a.md', 'TODO\n')
        changes = [_change('deleted.py', 'TODO'), _change('docs/a.md', 'TODO')]
        self.assertEqual(_find(changes, self.__cwd, [re.compile(r'^docs/')], _REGEXES), [])

    def test_same_line_in_each_file(self):
        # 同じ行でも、ファイルが違えばそれぞれ返す。同じファイルの同じ行は1件にまとめる。
        self.__write_file('a.py', 'print(1)\n')
        self.__write_file('b.py', 'print(1)\n')
        changes = [_change('a.py', 'print(1)'), _change('a.py', 'print(1)'), _change('b.py', 'print(1)')]
        self.assertEqual(
            _find(changes, self.__cwd, [], _REGEXES), [('a.py', 'print', 'print(1)'), ('b.py', 'print', 'print(1)')],
        )

    def test_paths_outside_cwd(self):
        # cwdの外のファイルは、`/` 区切りの絶対パスで示す。
        outside = tempfile.TemporaryDirectory(prefix='ghc-find-introduced-lines-outside-')
        self.addCleanup(outside.cleanup)
        path = os.path.join(os.path.realpath(outside.name), 'a.py')
        with open(path, 'w', encoding='utf-8') as file:
            file.write('TODO\n')
        self.assertEqual(
            _find([_change(path, 'TODO')], self.__cwd, [], _REGEXES), [(path.replace(os.sep, '/'), 'todo', 'TODO')],
        )


if __name__ == '__main__':
    run_tests()
