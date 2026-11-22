import json
import os
import runpy
import tempfile
import unittest
import uuid
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'debug_mode_state.py'))
_get_debug_mode = _utility['get_debug_mode']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook_test_output.py'))['run_tests']


class _TemporaryDirectoryTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-debug-mode-state-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.root = os.path.join(temporary.name, 'ghc-agent-refinement')
        self.session_id = str(uuid.uuid4())


class DebugModeTests(_TemporaryDirectoryTestCase):
    def __write_state(self, content, encoding='utf-8'):
        path = _utility['get_debug_mode_path'](self.session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding=encoding, newline='') as file:
            file.write(content)

    def test_state_path(self):
        self.assertEqual(
            _utility['get_debug_mode_path'](uuid.UUID(self.session_id)),
            os.path.join(self.root, 'hook-states', 'debug-mode', f'{self.session_id}.json'),
        )
        # 不正な値は、文字列にせずにセッションIDの検証へ渡す。
        for session_id, message in (
            (None, 'sessionId must be a non-empty UUID string.'),
            (0, 'sessionId must be a non-empty UUID string.'),
            ('not-a-uuid', 'sessionId must be a valid UUID.'),
        ):
            with self.subTest(session_id=session_id):
                with self.assertRaises(ValueError) as context:
                    _utility['get_debug_mode_path'](session_id)
                self.assertEqual(str(context.exception), message)

    def test_missing_state_is_disabled(self):
        self.assertIs(_get_debug_mode(self.session_id), False)

    def test_save_and_read(self):
        # 保存した状態を読めて、別のセッションには影響しない。
        state_path = _utility['get_debug_mode_path'](self.session_id)
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                _utility['save_debug_mode'](state_path, enabled)
                with open(state_path, encoding='utf-8') as file:
                    state = json.load(file)
                self.assertEqual(sorted(state), ['enabled', 'updatedAt'])
                self.assertIs(_get_debug_mode(self.session_id), enabled)
                self.assertIs(_get_debug_mode(str(uuid.uuid4())), False)

    def test_only_true_enables(self):
        for content, expected in (
            ('{"enabled":true}', True),
            ('{"enabled":false}', False),
            ('{"enabled":"true"}', False),
            ('{"enabled":1}', False),
            ('{}', False),
        ):
            with self.subTest(content=content):
                self.__write_state(content)
                self.assertIs(_get_debug_mode(self.session_id), expected)

    def test_reads_bom(self):
        self.__write_state('{"enabled":true}', encoding='utf-8-sig')
        self.assertIs(_get_debug_mode(self.session_id), True)

    def test_rejects_invalid_state(self):
        for content in ('[]', '{'):
            with self.subTest(content=content):
                self.__write_state(content)
                with self.assertRaises(ValueError):
                    _get_debug_mode(self.session_id)


class DebugLogDirectoryTests(_TemporaryDirectoryTestCase):
    def test_returns_session_directory_without_creating_it(self):
        # 前後の空白と大文字小文字が違っても、同じセッションは同じ保存先になる。
        expected = os.path.join(self.root, 'debug-logs', 'prompt-transformations', self.session_id)
        for spelling in (self.session_id, f' {self.session_id.upper()} '):
            with self.subTest(spelling=spelling):
                self.assertEqual(_utility['get_debug_log_directory']('prompt-transformations', spelling), expected)
        self.assertFalse(os.path.exists(self.root))

    def test_rejects_invalid_session_id(self):
        # パスの区切りを含む値で、ログの保存先の外を指させない。
        for session_id in (None, '', 'not-a-uuid', '../escape'):
            with self.subTest(session_id=session_id):
                with self.assertRaises(ValueError):
                    _utility['get_debug_log_directory']('prompt-transformations', session_id)


if __name__ == '__main__':
    _run_tests()
