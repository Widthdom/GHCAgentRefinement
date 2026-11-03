import os
import runpy
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from unittest.mock import patch

script_directory = os.path.dirname(os.path.abspath(__file__))
utility = runpy.run_path(os.path.join(script_directory, 'verified-packages-state.py'))

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class VerifiedPackagesStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-verified-packages-state-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.temp_directory = temporary.name
        self.state_path = utility['get_state_path'](str(uuid.uuid4()))

    def test_state_path(self):
        session_id = uuid.uuid4()
        self.assertEqual(
            utility['get_state_path'](f' {str(session_id).upper()} '),
            os.path.join(self.temp_directory, 'ghc-agent-refinement', 'hook-states', 'verified-packages',
                         f'{session_id}.sqlite3'),
        )
        for invalid in (None, '', '  ', 'not-a-uuid'):
            with self.subTest(session_id=invalid), self.assertRaises(ValueError):
                utility['get_state_path'](invalid)

    def test_normalize_name(self):
        normalize_name = utility['normalize_name']
        for ecosystem, name, expected in (
            (utility['PYPI'], 'Foo_Bar.baz--Qux', 'foo-bar-baz-qux'),
            (utility['NUGET'], 'Newtonsoft.Json', 'newtonsoft.json'),
            (utility['NPM'], '@Scope/Pkg_Name', '@scope/pkg_name'),
            # importするモジュール名は大文字小文字を区別するため変えない。
            (utility['PYTHON_MODULE'], 'PIL', 'PIL'),
        ):
            with self.subTest(ecosystem=ecosystem, name=name):
                self.assertEqual(normalize_name(ecosystem, name), expected)

    def test_record_and_get(self):
        self.assertEqual(utility['get_verified'](self.state_path), set())
        utility['record_verified'](self.state_path, [(utility['PYPI'], 'Foo_Bar'), (utility['NPM'], 'Left-Pad')])
        utility['record_verified'](self.state_path, [(utility['PYPI'], 'foo-bar')])
        self.assertEqual(utility['get_verified'](self.state_path), {('pypi', 'foo-bar'), ('npm', 'left-pad')})

    def test_get_without_table(self):
        os.makedirs(os.path.dirname(self.state_path))
        with closing(sqlite3.connect(self.state_path)):
            pass
        self.assertEqual(utility['get_verified'](self.state_path), set())


if __name__ == '__main__':
    run_tests()
