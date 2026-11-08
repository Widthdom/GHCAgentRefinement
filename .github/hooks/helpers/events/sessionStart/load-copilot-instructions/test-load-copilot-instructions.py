import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'load-copilot-instructions.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_real_instructions_path = os.path.join(os.path.dirname(_hooks_directory), 'copilot-instructions.md')
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


def _invoke_hook(script_path, cwd=None, env=None):
    # BOMの有無を検査できるよう、標準出力はバイト列のまま受け取る。
    return subprocess.run(
        [sys.executable, script_path], stdin=subprocess.DEVNULL, capture_output=True, cwd=cwd, env=env, timeout=30,
    )


class LoadCopilotInstructionsTests(unittest.TestCase):
    def __new_sandbox(self, suffix=''):
        # hookは自身の位置から指示ファイルを探すため、同じ構成を一時領域に作る。
        temporary = tempfile.TemporaryDirectory(prefix='ghc-load-copilot-instructions-test-', suffix=suffix)
        self.addCleanup(temporary.cleanup)
        hook_directory = os.path.join(
            temporary.name, '.github', 'hooks', 'helpers', 'events', 'sessionStart', 'load-copilot-instructions',
        )
        os.makedirs(hook_directory)
        script_path = shutil.copy(_hook_path, hook_directory)
        return temporary.name, script_path, os.path.join(temporary.name, '.github', 'copilot-instructions.md')

    def __invoke_with_instructions(self, data):
        _, script_path, instructions_path = self.__new_sandbox()
        with open(instructions_path, 'wb') as file:
            file.write(data)
        return _invoke_hook(script_path)

    def __assert_context(self, result, expected):
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stderr.decode('utf-8', 'replace'))
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = result.stdout.decode('utf-8')
        self.assertRegex(output, r'\A[^\r\n]+\n\Z')
        self.assertEqual(json.loads(output), {'additionalContext': expected})

    def __assert_error(self, result, exit_code, error):
        self.assertEqual((result.returncode, result.stdout), (exit_code, b''))
        self.assertIn(error, result.stderr.decode('utf-8'))

    def test_real_instructions(self):
        with open(_real_instructions_path, 'rb') as file:
            expected = file.read().decode('utf-8-sig')
        self.__assert_context(_invoke_hook(_hook_path), expected)

    def test_content_preservation(self):
        cases = (
            ('Japanese text', '# 指示\n- 応答は日本語を用いる。\n'),
            ('CRLF line endings', 'line1\r\nline2\r\n'),
            ('mixed line endings', 'line1\rline2\r\nline3\n'),
            ('leading and trailing whitespace is preserved', ' \t\n指示\r\n \t'),
            ('Unicode separators', 'a\u0085b c d'),
            ('embedded control characters', 'a\x00\x1cb'),
            ('embedded BOM is preserved', 'a﻿b'),
            ('JSON special characters', "quote \" backslash \\ tab \t slash / <tag> & 'single'"),
            ('emoji (surrogate pair)', '絵文字 😀 を含む'),
        )
        for name, content in cases:
            with self.subTest(name=name):
                self.__assert_context(self.__invoke_with_instructions(content.encode('utf-8')), content)
        # 先頭のUTF-8 BOMは内容に含めない。
        self.__assert_context(self.__invoke_with_instructions(b'\xef\xbb\xbf' + '# 指示\n'.encode('utf-8')), '# 指示\n')

    def test_missing_or_empty_instructions(self):
        _, script_path, instructions_path = self.__new_sandbox()
        self.__assert_error(_invoke_hook(script_path), 2, 'instructions file not found')
        os.mkdir(instructions_path)
        self.__assert_error(_invoke_hook(script_path), 2, 'instructions file not found')
        cases = (
            ('empty', b''),
            ('whitespace only', b' \t\r\n'),
            ('Unicode whitespace only', '\u0085   　'.encode('utf-8')),
            ('Python whitespace control characters', b'\x1c\x1d\x1e\x1f'),
            ('BOM only', b'\xef\xbb\xbf'),
        )
        for name, data in cases:
            with self.subTest(name=name):
                self.__assert_error(self.__invoke_with_instructions(data), 2, 'instructions file is empty')

    def test_encoding_errors(self):
        cases = (
            ('invalid UTF-8', b'\xff'),
            ('truncated UTF-8', b'\xe3\x81'),
            ('UTF-8 encoded surrogate', b'\xed\xa0\x80'),
            ('UTF-16 LE BOM', b'\xff\xfe' + '指示'.encode('utf-16-le')),
            ('UTF-16 BE BOM', b'\xfe\xff' + '指示'.encode('utf-16-be')),
            ('UTF-32 LE BOM', b'\xff\xfe\x00\x00' + '指示'.encode('utf-32-le')),
            ('UTF-32 BE BOM', b'\x00\x00\xfe\xff' + '指示'.encode('utf-32-be')),
        )
        for name, data in cases:
            with self.subTest(name=name):
                self.__assert_error(self.__invoke_with_instructions(data), 2, 'Failed to read Copilot instructions file')

    def test_unicode_paths_and_streams(self):
        root, script_path, instructions_path = self.__new_sandbox('-日本語 path')
        content = '# 指示 😀\r\n'
        with open(instructions_path, 'wb') as file:
            file.write(content.encode('utf-8'))
        # 既定の入出力がASCIIでも、UTF-8で出力する。
        ascii_env = dict(os.environ, PYTHONIOENCODING='ascii:strict')
        self.__assert_context(_invoke_hook(script_path, cwd=tempfile.gettempdir(), env=ascii_env), content)
        self.__assert_context(_invoke_hook(os.path.relpath(script_path, root), cwd=root), content)
        os.remove(instructions_path)
        result = _invoke_hook(script_path, env=ascii_env)
        self.assertEqual((result.returncode, result.stdout), (2, b''))
        self.assertEqual(result.stderr.decode('utf-8'), f'Copilot instructions file not found: {instructions_path}\n')


if __name__ == '__main__':
    _run_tests()
