import os
import runpy
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _script_directory, 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class ReadStoredFileContentForWriteComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-read-stored-file-content-for-write-comparison-test-')
        self.addCleanup(temporary.cleanup)
        self.__directory = temporary.name
        self.__path = os.path.join(temporary.name, 'a.txt')

    def __write(self, content):
        with open(self.__path, 'wb') as file:
            file.write(content)

    def test_reads_utf8_with_newlines_normalized(self):
        self.__write('日本語\r\nb\rc\n'.encode('utf-8'))
        self.assertEqual(_read_stored_file_content_for_write_comparison(self.__path), '日本語\nb\nc\n')

    def test_replaces_undecodable_bytes(self):
        self.__write(b'a\xffb')
        self.assertEqual(_read_stored_file_content_for_write_comparison(self.__path), 'a\ufffdb')

    def test_empty_file(self):
        self.__write(b'')
        self.assertEqual(_read_stored_file_content_for_write_comparison(self.__path), '')

    def test_unreadable_paths_return_none(self):
        for path in (self.__path, self.__directory):
            with self.subTest(path=path):
                self.assertIsNone(_read_stored_file_content_for_write_comparison(path))


if __name__ == '__main__':
    _run_tests()
