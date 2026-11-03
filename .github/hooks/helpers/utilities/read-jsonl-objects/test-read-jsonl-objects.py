import os
import runpy
import sys
import tempfile
import unittest

script_directory = os.path.dirname(os.path.abspath(__file__))
read_jsonl_objects = runpy.run_path(os.path.join(script_directory, 'read-jsonl-objects.py'))['read_jsonl_objects']

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class ReadJsonlObjectsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-read-jsonl-objects-test-')
        self.addCleanup(temporary.cleanup)
        self.path = os.path.join(temporary.name, 'events.jsonl')

    def write(self, content, encoding='utf-8'):
        with open(self.path, 'w', encoding=encoding, newline='') as file:
            file.write(content)

    def test_reads_objects_skipping_blank_lines_and_bom(self):
        for encoding in ('utf-8', 'utf-8-sig'):
            with self.subTest(encoding=encoding):
                self.write('{"a":1}\n\n  \r\n{"b":"日本語"}\r\n', encoding)
                self.assertEqual(list(read_jsonl_objects(self.path)), [{'a': 1}, {'b': '日本語'}])

    def test_rejects_invalid_lines_with_line_number(self):
        for content, message in (
            ('{"a":1}\n{"a":\n', 'Invalid JSON at line 2'),
            ('{"a":1}\n\n[1]\n', 'Expected a JSON object at line 3.'),
            ('"text"\n', 'Expected a JSON object at line 1.'),
        ):
            with self.subTest(content=content):
                self.write(content)
                with self.assertRaises(ValueError) as context:
                    list(read_jsonl_objects(self.path))
                self.assertIn(message, str(context.exception))

    def test_yields_lazily_before_invalid_line(self):
        # 不正な行より前のオブジェクトは、その行を読むまでに受け取れる。
        self.write('{"a":1}\nbroken\n')
        objects = read_jsonl_objects(self.path)
        self.assertEqual(next(objects), {'a': 1})
        with self.assertRaises(ValueError):
            next(objects)

    def test_missing_file(self):
        with self.assertRaises(OSError):
            list(read_jsonl_objects(self.path))


if __name__ == '__main__':
    run_tests()
