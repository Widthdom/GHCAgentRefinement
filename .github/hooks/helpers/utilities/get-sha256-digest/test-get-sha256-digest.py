import hashlib
import os
import runpy
import unittest
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'get-sha256-digest.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


class GetSha256DigestTests(unittest.TestCase):
    def test_text_digest_is_sha256_of_utf8(self):
        self.assertEqual(_utility['get_text_sha256_digest']('値'), hashlib.sha256('値'.encode('utf-8')).hexdigest())
        self.assertNotEqual(_utility['get_text_sha256_digest']('a'), _utility['get_text_sha256_digest']('A'))

    def test_path_digest_normalizes_case_like_the_os(self):
        path = os.path.join('Dir', 'File.py')
        self.assertEqual(
            _utility['get_path_sha256_digest'](path), _utility['get_text_sha256_digest'](os.path.normcase(path)),
        )
        # 大文字小文字を区別しないOSでは、表記が違っても同じダイジェストになる。
        with patch.object(os.path, 'normcase', str.lower):
            self.assertEqual(
                _utility['get_path_sha256_digest']('Dir/File.py'), _utility['get_path_sha256_digest']('dir/file.py'),
            )
            self.assertEqual(
                _utility['get_path_sha256_digest']('Dir/File.py'), _utility['get_text_sha256_digest']('dir/file.py'),
            )


if __name__ == '__main__':
    _run_tests()
