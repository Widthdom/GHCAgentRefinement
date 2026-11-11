import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_split_bash_lines = runpy.run_path(os.path.join(_script_directory, 'split-bash-lines.py'))['split_bash_lines']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class SplitBashLinesTests(unittest.TestCase):
    def test_splits_words_and_operators(self):
        self.assertEqual(
            list(_split_bash_lines('a "b c" | d>>e; f 2>&1')),
            [['a', 'b c', '|', 'd', '>>', 'e', ';', 'f', '2', '>&', '1']],
        )

    def test_joins_logical_lines(self):
        for command, expected in (
            ('a\nb', [['a'], ['b']]),
            ('a\r\nb', [['a'], ['b']]),
            ('a \\\nb', [['a', 'b']]),
            ('a \\\\\nb', [['a', '\\'], ['b']]),
            ("a 'b\nc' d", [['a', 'b\nc', 'd']]),
        ):
            with self.subTest(command=command):
                self.assertEqual(list(_split_bash_lines(command)), expected)

    def test_skips_heredoc_bodies(self):
        for command, delimiter in (
            ("cat <<'EOF' > x\n| body\nEOF\nb", 'EOF'),
            ('cat <<"EOF" > x\n| body\nEOF\nb', 'EOF'),
            ('cat <<EOF > x\n| body\nEOF\nb', 'EOF'),
            # `<<-` は区切りの行の先頭のタブを無視する。`-` は演算子の文字ではないため、区切り語の字句に付く。
            ('cat <<-EOF > x\n| body\n\tEOF\nb', '-EOF'),
        ):
            with self.subTest(command=command):
                self.assertEqual(list(_split_bash_lines(command)), [['cat', '<<', delimiter, '>', 'x'], ['b']])

    def test_here_string_is_not_heredoc(self):
        self.assertEqual(list(_split_bash_lines('cat <<< EOF\nb')), [['cat', '<<<', 'EOF'], ['b']])

    def test_quoted_or_arithmetic_shift_is_not_heredoc(self):
        for command, first in (
            ('echo "a << b"\necho x > y', ['echo', 'a << b']),
            ("echo 'a <<b'\necho x > y", ['echo', 'a <<b']),
            ('x=$((1 << 2))\necho x > y', ['x=$', '((', '1', '<<', '2', '))']),
            ('echo $(( 1<<2 ));\necho x > y', ['echo', '$', '((', '1', '<<', '2', '));']),
            ('((x <<= 1))\necho x > y', ['((', 'x', '<<', '=', '1', '))']),
        ):
            with self.subTest(command=command):
                self.assertEqual(list(_split_bash_lines(command)), [first, ['echo', 'x', '>', 'y']])

    def test_heredoc_after_arithmetic(self):
        self.assertEqual(
            list(_split_bash_lines('echo $((1<<2)) <<EOF\n| body\nEOF\nb')),
            [['echo', '$', '((', '1', '<<', '2', '))', '<<', 'EOF'], ['b']],
        )

    def test_strips_comments_at_word_start_only(self):
        for command, expected in (
            ('sed -i s#a#b# CLAUDE.md', [['sed', '-i', 's#a#b#', 'CLAUDE.md']]),
            ('echo a#b > CLAUDE.md', [['echo', 'a#b', '>', 'CLAUDE.md']]),
            ('echo ${#x} $#', [['echo', '${#x}', '$#']]),
            ('echo "# a" \'# b\' \\#c', [['echo', '# a', '# b', '#c']]),
            ('echo a # > CLAUDE.md', [['echo', 'a']]),
            ('echo a;# > CLAUDE.md', [['echo', 'a', ';']]),
            ('# cat <<EOF\nb', [[], ['b']]),
            ('echo a # \\\nb', [['echo', 'a'], ['b']]),
            ("echo 'a\n# b' # c\nd", [['echo', 'a\n# b'], ['d']]),
        ):
            with self.subTest(command=command):
                self.assertEqual(list(_split_bash_lines(command)), expected)

    def test_drops_unclosed_quotes(self):
        self.assertEqual(list(_split_bash_lines("a\nb 'c")), [['a']])


if __name__ == '__main__':
    _run_tests()
