import contextlib
import io
import os
import runpy
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'write-hook-output.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


def _capture(name, text):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        _utility[name](text)
    return stdout.getvalue()


class WriteHookOutputTests(unittest.TestCase):
    def test_writes_each_output_as_one_compact_json_line(self):
        for name, expected in (
            ('write_deny_decision', '{"permissionDecision":"deny","permissionDecisionReason":"理由: a"}\n'),
            ('write_block_decision', '{"decision":"block","reason":"理由: a"}\n'),
            ('write_additional_context', '{"additionalContext":"理由: a"}\n'),
        ):
            with self.subTest(name=name):
                self.assertEqual(_capture(name, '理由: a'), expected)

    def test_escapes_json_special_characters(self):
        # 改行・引用符・バックスラッシュはエスケープし、出力を1行に保つ。
        self.assertEqual(
            _capture('write_block_decision', 'a\n"b"\\c'),
            '{"decision":"block","reason":"a\\n\\"b\\"\\\\c"}\n',
        )


if __name__ == '__main__':
    _run_tests()
