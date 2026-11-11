import json
import os
import runpy
import tempfile
import unittest
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'write-json-atomically.py'))
_write_json_atomically = _utility['write_json_atomically']

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class WriteJsonAtomicallyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-write-json-atomically-test-')
        self.addCleanup(temporary.cleanup)
        self.__directory = os.path.join(temporary.name, 'states')
        self.__path = os.path.join(self.__directory, 'state.json')

    def __read_bytes(self):
        with open(self.__path, 'rb') as file:
            return file.read()

    def test_writes_compact_utf8_json_and_creates_directory(self):
        _write_json_atomically(self.__path, {'enabled': True, 'name': '日本語'})
        self.assertEqual(self.__read_bytes(), '{"enabled":true,"name":"日本語"}'.encode('utf-8'))
        self.assertEqual(os.listdir(self.__directory), ['state.json'])

    def test_replaces_existing_file(self):
        _write_json_atomically(self.__path, {'value': 'old and longer'})
        _write_json_atomically(self.__path, {'value': 1})
        self.assertEqual(self.__read_bytes(), b'{"value":1}')
        self.assertEqual(os.listdir(self.__directory), ['state.json'])

    def test_keeps_previous_file_and_removes_temporary_file_on_failure(self):
        _write_json_atomically(self.__path, {'value': 1})
        with self.assertRaises(TypeError):
            _write_json_atomically(self.__path, {'value': object()})
        with patch.object(_utility['os'], 'replace', side_effect=OSError('replace failed')):
            with self.assertRaisesRegex(OSError, 'replace failed'):
                _write_json_atomically(self.__path, {'value': 2})
        self.assertEqual(json.loads(self.__read_bytes()), {'value': 1})
        self.assertEqual(os.listdir(self.__directory), ['state.json'])


if __name__ == '__main__':
    _run_tests()
