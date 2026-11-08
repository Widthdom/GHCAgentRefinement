import io
import os
import runpy
import tempfile
import unittest
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'read-hook-input.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


def _read(raw):
    with patch('sys.stdin', io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8')):
        return _utility['read_hook_input']()


class ReadHookInputTests(unittest.TestCase):
    def test_reads_utf8_json_object_from_bytes(self):
        # 標準入力の既定のエンコーディングによらず、バイト列をUTF-8で復号する。
        self.assertEqual(_read('{"prompt":"日本語\\r\\n","cwd":"/a"}'.encode('utf-8')), {'prompt': '日本語\r\n', 'cwd': '/a'})

    def test_rejects_invalid_input(self):
        for raw, error, message in (
            (b'', ValueError, 'hook input is empty.'),
            (b' \t\r\n', ValueError, 'hook input is empty.'),
            (b'{', ValueError, 'Expecting property name'),
            (b'{"a":"\xff"}', UnicodeError, "'utf-8' codec can't decode"),
            (b'[]', ValueError, 'hook input must be a JSON object.'),
            (b'null', ValueError, 'hook input must be a JSON object.'),
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(error) as context:
                    _read(raw)
                self.assertIn(message, str(context.exception))

    def test_get_cwd(self):
        cwd = os.path.abspath(tempfile.gettempdir())
        self.assertEqual(_utility['get_cwd']({'cwd': cwd}), cwd)
        for hook_input, message in (
            ({}, 'cwd must be a non-empty string.'),
            ({'cwd': ' '}, 'cwd must be a non-empty string.'),
            ({'cwd': ['/a']}, 'cwd must be a non-empty string.'),
            ({'cwd': 'relative/path'}, 'cwd must be an absolute path.'),
        ):
            with self.subTest(hook_input=hook_input):
                with self.assertRaises(ValueError) as context:
                    _utility['get_cwd'](hook_input)
                self.assertEqual(str(context.exception), message)

    def test_get_tool_call(self):
        # toolArgsは値の形式を問わず、そのまま返す。
        for tool_args in ({'command': 'ls'}, '{"command":"ls"}', None):
            with self.subTest(tool_args=tool_args):
                self.assertEqual(
                    _utility['get_tool_call']({'toolName': 'bash', 'toolArgs': tool_args}), ('bash', tool_args),
                )
        for hook_input, message in (
            ({'toolArgs': {}}, 'toolName must be a non-empty string.'),
            ({'toolName': ' ', 'toolArgs': {}}, 'toolName must be a non-empty string.'),
            ({'toolName': 42, 'toolArgs': {}}, 'toolName must be a non-empty string.'),
            ({'toolName': 'bash'}, 'toolArgs is required.'),
        ):
            with self.subTest(hook_input=hook_input):
                with self.assertRaises(ValueError) as context:
                    _utility['get_tool_call'](hook_input)
                self.assertEqual(str(context.exception), message)

    def test_get_transcript_path(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-read-hook-input-test-')
        self.addCleanup(temporary.cleanup)
        transcript_path = os.path.join(temporary.name, 'events.jsonl')
        with open(transcript_path, 'w', encoding='utf-8', newline='\n'):
            pass
        self.assertEqual(_utility['get_transcript_path']({'transcriptPath': transcript_path}), transcript_path)
        for hook_input, message in (
            ({}, 'transcriptPath must be a non-empty string.'),
            ({'transcriptPath': ' '}, 'transcriptPath must be a non-empty string.'),
            ({'transcriptPath': temporary.name}, 'transcript path is not a file'),
            ({'transcriptPath': os.path.join(temporary.name, 'missing.jsonl')}, 'transcript path is not a file'),
        ):
            with self.subTest(hook_input=hook_input):
                with self.assertRaises(ValueError) as context:
                    _utility['get_transcript_path'](hook_input)
                self.assertIn(message, str(context.exception))


if __name__ == '__main__':
    _run_tests()
