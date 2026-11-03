import json
import os
import runpy
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

script_directory = os.path.dirname(os.path.abspath(__file__))
utility = runpy.run_path(os.path.join(script_directory, 'hook-change-request-state.py'))

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class HookChangeRequestStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-hook-change-request-state-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.temp_directory = temporary.name
        self.state_path = utility['get_state_path'](str(uuid.uuid4()))

    def write_state(self, content):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, 'w', encoding='utf-8', newline='') as file:
            file.write(content)

    def test_state_path(self):
        session_id = uuid.uuid4()
        self.assertEqual(
            utility['get_state_path'](f' {str(session_id).upper()} '),
            os.path.join(self.temp_directory, 'ghc-agent-refinement', 'hook-states', 'hook-change-requests',
                         f'{session_id}.json'),
        )
        for invalid in (None, '', '  ', 'not-a-uuid'):
            with self.subTest(session_id=invalid), self.assertRaises(ValueError):
                utility['get_state_path'](invalid)

    def test_save_and_read_permission(self):
        self.assertIs(utility['is_permitted'](self.state_path), False)
        utility['save_permitted'](self.state_path)
        self.assertIs(utility['is_permitted'](self.state_path), True)
        # プロンプトの内容は保存せず、更新時刻と許可だけを保存する。
        with open(self.state_path, encoding='utf-8') as file:
            self.assertEqual(sorted(json.load(file)), ['permitted', 'updatedAt'])

    def test_only_true_permits(self):
        for content in ('{"permitted":false}', '{"permitted":"true"}', '{}'):
            with self.subTest(content=content):
                self.write_state(content)
                self.assertIs(utility['is_permitted'](self.state_path), False)

    def test_rejects_invalid_state(self):
        for content in ('[]', '{'):
            with self.subTest(content=content):
                self.write_state(content)
                with self.assertRaises(ValueError):
                    utility['is_permitted'](self.state_path)


if __name__ == '__main__':
    run_tests()
