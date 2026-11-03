import os
import runpy
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_read_jsonl_objects = runpy.run_path(os.path.join(_script_directory, 'read-jsonl-objects.py'))['read_jsonl_objects']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class ReadJsonlObjectsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-read-jsonl-objects-test-')
        self.addCleanup(temporary.cleanup)
        self.__path = os.path.join(temporary.name, 'events.jsonl')

    def __write(self, content, encoding='utf-8'):
        with open(self.__path, 'w', encoding=encoding, newline='') as file:
            file.write(content)

    def test_reads_objects_skipping_blank_lines_and_bom(self):
        for encoding in ('utf-8', 'utf-8-sig'):
            with self.subTest(encoding=encoding):
                self.__write('{"a":1}\n\n  \r\n{"b":"日本語"}\r\n', encoding)
                self.assertEqual(list(_read_jsonl_objects(self.__path)), [{'a': 1}, {'b': '日本語'}])

    def test_rejects_invalid_lines_with_line_number(self):
        for content, message in (
            ('{"a":1}\n{"a":\n', 'Invalid JSON at line 2'),
            ('{"a":1}\n\n[1]\n', 'Expected a JSON object at line 3.'),
            ('"text"\n', 'Expected a JSON object at line 1.'),
        ):
            with self.subTest(content=content):
                self.__write(content)
                with self.assertRaises(ValueError) as context:
                    list(_read_jsonl_objects(self.__path))
                self.assertIn(message, str(context.exception))

    def test_yields_lazily_before_invalid_line(self):
        # 不正な行より前のオブジェクトは、その行を読むまでに受け取れる。
        self.__write('{"a":1}\nbroken\n')
        objects = _read_jsonl_objects(self.__path)
        self.assertEqual(next(objects), {'a': 1})
        with self.assertRaises(ValueError):
            next(objects)

    def test_missing_file(self):
        with self.assertRaises(OSError):
            list(_read_jsonl_objects(self.__path))


if __name__ == '__main__':
    run_tests()
