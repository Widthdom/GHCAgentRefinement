import os
import sys
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_directory)
import verified_packages_state as _utility

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests
from temporary_state_test_case import TemporaryStateTestCase as _TemporaryStateTestCase


class VerifiedPackagesStateTests(_TemporaryStateTestCase):
    def setUp(self):
        super().setUp()
        self.__state_path = _utility.get_state_path(str(uuid.uuid4()))

    def test_state_path(self):
        self.assert_state_path(_utility.get_state_path, 'verified_packages')

    def test_normalize_name(self):
        normalize_name = _utility.normalize_name
        for ecosystem, name, expected in (
            (_utility._PYPI, 'Foo_Bar.baz--Qux', 'foo-bar-baz-qux'),
            (_utility._NUGET, 'Newtonsoft.Json', 'newtonsoft.json'),
            (_utility._NPM, '@Scope/Pkg_Name', '@scope/pkg_name'),
            # importするモジュール名は大文字小文字を区別するため変えない。
            (_utility._PYTHON_MODULE, 'PIL', 'PIL'),
        ):
            with self.subTest(ecosystem=ecosystem, name=name):
                self.assertEqual(normalize_name(ecosystem, name), expected)

    def test_record_and_get(self):
        self.assertEqual(_utility.get_verified(self.__state_path), set())
        _utility.record_verified(self.__state_path, [(_utility._PYPI, 'Foo_Bar'), (_utility._NPM, 'Left-Pad')])
        _utility.record_verified(self.__state_path, [(_utility._PYPI, 'foo-bar')])
        self.assertEqual(_utility.get_verified(self.__state_path), {('pypi', 'foo-bar'), ('npm', 'left-pad')})


if __name__ == '__main__':
    _run_tests()
