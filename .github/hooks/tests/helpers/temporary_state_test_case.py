import os
import tempfile
import unittest
import uuid
from unittest.mock import patch


class TemporaryStateTestCase(unittest.TestCase):
    # テストごとの一時フォルダーを作り、このプロセスの `tempfile.gettempdir()` がそのフォルダーを返すよう差し替える。
    # hookやutilityが書く状態・ログを、実際の一時フォルダーに残さないため。
    # temp_directory：テストごとの一時フォルダー。Windowsの短縮名（RUNNER~1など）やシンボリックリンクを解決した実体のパス。
    # temp_environment：子プロセスで起動するhookにも同じ一時フォルダーを使わせる環境変数。
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc_hook_state_test_')
        self.addCleanup(temporary.cleanup)
        self.temp_directory = os.path.realpath(temporary.name)
        tempdir = patch.object(tempfile, 'tempdir', self.temp_directory)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.temp_environment = dict(
            os.environ, TMPDIR=self.temp_directory, TEMP=self.temp_directory, TMP=self.temp_directory,
        )

    def assert_state_path(self, get_state_path, state_name, extension='.sqlite3'):
        # get_state_path：セッションIDから状態の保存先を返す関数。
        # 前後の空白と大文字小文字が違っても同じ保存先になり、UUIDでないセッションIDはValueErrorになることを検証する。
        session_id = uuid.uuid4()
        self.assertEqual(
            get_state_path(f' {str(session_id).upper()} '),
            os.path.join(self.temp_directory, 'ghc_agent_refinement', 'hook_states', state_name, f'{session_id}{extension}'),
        )
        for invalid in (None, '', '  ', 'not-a-uuid'):
            with self.subTest(session_id=invalid), self.assertRaises(ValueError):
                get_state_path(invalid)
