import json
import os
import runpy
import shutil
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'check-test-files.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class CheckTestFilesTests(unittest.TestCase):
    def setUp(self):
        self.__directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.__directory, ignore_errors=True)

    def __write_definitions(self, content):
        path = os.path.join(self.__directory, 'definitions.json')
        with open(path, 'w', encoding='utf-8', newline='\n') as definitions_file:
            definitions_file.write(content)
        return path

    def test_definitions_are_valid_json(self):
        with open(os.path.join(_script_directory, 'test-file-definitions.json'), encoding='utf-8') as definitions_file:
            definitions = json.load(definitions_file)
        self.assertTrue(definitions['testFilePatterns'])

    def test_test_files(self):
        patterns = _utility['load_test_file_patterns']()
        for path in (
            'tests/app.py', 'src/__tests__/app.js', 'spec/app_spec.rb', 'Test/App.cs', 'App.Tests/Calculator.cs',
            'App.UnitTests/Calculator.cs', 'src/CalculatorTests.cs', 'src/CalculatorTest.java', 'src/AppTest.kt',
            'test_app.py', 'pkg/test-app.py', 'test-hook.ps1', 'app_test.py', 'app_test.go', 'app_test.rs',
            'app.test.ts', 'app.spec.js', 'app.test.mjs', 'app.spec.tsx',
        ):
            with self.subTest(path=path):
                self.assertTrue(_utility['is_test_file'](patterns, path))
        for path in (
            'src/app.py', 'src/testing.py', 'src/contest/app.py', 'attest.py', 'src/Testing.cs', 'app.tests.md',
            'src/latest.ts', 'docs/test.md', 'test-file-definitions.json',
        ):
            with self.subTest(path=path):
                self.assertFalse(_utility['is_test_file'](patterns, path))

    def test_case_sensitive_class_suffix(self):
        # .NET・Java・Kotlinのテストクラスは、大文字の `Test` で終わる名前だけをテストファイルとみなす。
        patterns = _utility['load_test_file_patterns']()
        self.assertTrue(_utility['is_test_file'](patterns, 'src/AppTests.cs'))
        self.assertFalse(_utility['is_test_file'](patterns, 'src/Apptests.cs'))

    def test_custom_definitions(self):
        patterns = _utility['load_test_file_patterns'](self.__write_definitions('﻿{"testFilePatterns": ["^check/"]}'))
        self.assertTrue(_utility['is_test_file'](patterns, 'check/app.py'))
        self.assertFalse(_utility['is_test_file'](patterns, 'tests/app.py'))

    def test_invalid_definitions(self):
        for name, content in (
            ('invalid json', '{'),
            ('not an object', '[]'),
            ('missing patterns', '{}'),
            ('empty patterns', '{"testFilePatterns": []}'),
            ('blank pattern', '{"testFilePatterns": [" "]}'),
            ('non-string pattern', '{"testFilePatterns": [1]}'),
            ('invalid regex', '{"testFilePatterns": ["("]}'),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _utility['load_test_file_patterns'](self.__write_definitions(content))

    def test_missing_definitions(self):
        with self.assertRaises(OSError):
            _utility['load_test_file_patterns'](os.path.join(self.__directory, 'missing.json'))


if __name__ == '__main__':
    _run_tests()
