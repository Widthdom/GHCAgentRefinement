import json
import os
import sys
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_directory)
import hook_change_request_state as _utility

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests
from temporary_state_test_case import TemporaryStateTestCase as _TemporaryStateTestCase


class HookChangeRequestStateTests(_TemporaryStateTestCase):
    def setUp(self):
        super().setUp()
        self.__state_path = _utility.get_state_path(str(uuid.uuid4()))

    def __write_state(self, content):
        os.makedirs(os.path.dirname(self.__state_path), exist_ok=True)
        with open(self.__state_path, 'w', encoding='utf-8', newline='') as file:
            file.write(content)

    def test_state_path(self):
        self.assert_state_path(_utility.get_state_path, 'hook_change_requests', '.json')

    def test_save_and_read_permission(self):
        self.assertIs(_utility.is_permitted(self.__state_path), False)
        _utility.save_permitted(self.__state_path)
        self.assertIs(_utility.is_permitted(self.__state_path), True)
        # プロンプトの内容は保存せず、更新時刻と許可だけを保存する。
        with open(self.__state_path, encoding='utf-8') as file:
            self.assertEqual(sorted(json.load(file)), ['permitted', 'updatedAt'])

    def test_only_true_permits(self):
        for content in ('{"permitted":false}', '{"permitted":"true"}', '{}'):
            with self.subTest(content=content):
                self.__write_state(content)
                self.assertIs(_utility.is_permitted(self.__state_path), False)

    def test_rejects_invalid_state(self):
        for content in ('[]', '{'):
            with self.subTest(content=content):
                self.__write_state(content)
                with self.assertRaises(ValueError):
                    _utility.is_permitted(self.__state_path)


if __name__ == '__main__':
    _run_tests()
