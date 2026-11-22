import contextlib
import io
import os
import sys
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_directory)
import write_hook_output as _utility

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests


def _capture(name, text):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        getattr(_utility, name)(text)
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
