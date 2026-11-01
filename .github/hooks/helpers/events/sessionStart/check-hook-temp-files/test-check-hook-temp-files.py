import json
import os
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'check-hook-temp-files.py')
helper_path = os.path.join(script_directory, 'hook-temp-files.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
# 判定基準を借りる削除スクリプトも検証し、hookの移動・改名による参照切れを検出する。
clean_script_path = os.path.join(
    os.path.dirname(hooks_directory), 'skills', 'clean-hook-temp-files', 'scripts', 'clean-hook-temp-files.py',
)
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

STALE_SECONDS = 48 * 60 * 60 + 60
FRESH_SECONDS = 48 * 60 * 60 - 60


class CheckHookTempFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-check-hook-temp-files-test-')
        self.addCleanup(temporary.cleanup)
        self.temp = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.temp, 'TEMP': self.temp, 'TMP': self.temp})
        self.root = os.path.join(self.temp, 'ghc-agent-refinement')
        self.helper = runpy.run_path(helper_path)
        self.sequence = 0

    @staticmethod
    def set_age(path, age_seconds):
        modified = time.time() - age_seconds
        os.utime(path, (modified, modified))

    def create_file(self, relative_path, age_seconds, size=10):
        path = os.path.join(self.root, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as file:
            file.write(b'x' * size)
        self.set_age(path, age_seconds)
        return path

    def create_files(self, directory, count, age_seconds=STALE_SECONDS):
        for _ in range(count):
            self.sequence += 1
            self.create_file(os.path.join(directory, f'{self.sequence}.sqlite3'), age_seconds, size=0)

    def create_layout(self):
        # 48時間を超えて更新のないファイルと、それ以外のファイルを、分類の内外に配置する。
        stale = [
            self.create_file(os.path.join('hook-states', 'edit-oscillation', 'a.sqlite3'), STALE_SECONDS),
            self.create_file(os.path.join('hook-states', 'debug-mode', 'b.json'), STALE_SECONDS),
            self.create_file(os.path.join('debug-logs', 'prompt-transformations', 'session', 'c.txt'), STALE_SECONDS),
        ]
        kept = [
            self.create_file(os.path.join('hook-states', 'edit-oscillation', 'fresh.sqlite3'), FRESH_SECONDS),
            self.create_file(os.path.join('debug-mode', 'legacy.json'), STALE_SECONDS),
            self.create_file('root.txt', STALE_SECONDS),
        ]
        # ファイルを作るとディレクトリの更新時刻も変わるため、最後に古くする。
        for directory in (
            os.path.join('debug-logs', 'prompt-transformations', 'session'),
            os.path.join('debug-logs', 'prompt-transformations'),
            os.path.join('hook-states', 'debug-mode'),
        ):
            self.set_age(os.path.join(self.root, directory), STALE_SECONDS)
        return stale, kept

    def invoke_hook(self):
        return subprocess.run(
            [sys.executable, hook_path], input=b'{"source":"new","sessionId":"x","timestamp":0,"cwd":"/"}',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=30,
        )

    def run_clean(self, *arguments):
        return subprocess.run(
            [sys.executable, clean_script_path, *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=30,
        )

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_warning(self, result, count):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        self.assertRegex(result.stdout.decode('utf-8'), r'^[^\r\n]+\r?\n$')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'additionalContext'})
        self.assertIn(f'{count} temporary files under {self.root} ', output['additionalContext'])
        self.assertIn('/clean-hook-temp-files', output['additionalContext'])

    # 警告hook

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'sessionStart.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/sessionStart/check-hook-temp-files/check-hook-temp-files.py'
        entries = [
            entry for entry in configuration['hooks']['sessionStart']
            if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')

    def test_warns_at_threshold_across_categories(self):
        self.create_files(os.path.join('hook-states', 'edit-oscillation'), 200)
        self.create_files(os.path.join('hook-states', 'debug-mode'), 100)
        self.create_files(os.path.join('debug-logs', 'prompt-transformations', 'session'), 199)
        self.assert_silent(self.invoke_hook())
        self.create_files(os.path.join('debug-logs', 'prompt-transformations', 'other-session'), 1)
        self.assert_warning(self.invoke_hook(), 500)

    def test_counts_only_stale_files_in_categories(self):
        self.create_files(os.path.join('hook-states', 'known-files'), 499)
        # 48時間以内に更新されたファイルと、分類外のファイルは数えない。
        self.create_files(os.path.join('hook-states', 'known-files'), 50, age_seconds=FRESH_SECONDS)
        self.create_files('debug-mode', 50)
        self.create_files('', 50)
        self.create_files(os.path.join('..', 'other-tool'), 50)
        self.assert_silent(self.invoke_hook())
        self.create_files(os.path.join('hook-states', 'known-files'), 1)
        self.assert_warning(self.invoke_hook(), 500)

    def test_missing_directories_are_silent(self):
        self.assert_silent(self.invoke_hook())
        os.makedirs(os.path.join(self.root, 'hook-states'))
        self.assert_silent(self.invoke_hook())

    # 判定基準（hook-temp-files.py）

    def test_find_stale_files(self):
        stale, _ = self.create_layout()
        found = self.helper['find_stale_files'](self.root, time.time())
        self.assertEqual(sorted(path for _, path, _ in found), sorted(stale))
        # 分類は、OSによらず `/` 区切りで示される。
        self.assertEqual({group for group, _, _ in found}, {
            'hook-states/edit-oscillation',
            'hook-states/debug-mode',
            'debug-logs/prompt-transformations/session',
        })
        self.assertTrue(all(size == 10 for _, _, size in found))
        self.assertEqual(self.helper['find_stale_files'](os.path.join(self.temp, 'missing'), time.time()), [])

    def test_temp_root_uses_temp_directory(self):
        with patch('tempfile.gettempdir', return_value=self.temp):
            self.assertEqual(self.helper['get_temp_root'](), self.root)

    def test_delete_failure_is_reported(self):
        stale, _ = self.create_layout()
        original_remove = os.remove

        def remove(path):
            if path == stale[0]:
                raise PermissionError(13, 'Permission denied')
            original_remove(path)

        with patch('os.remove', side_effect=remove):
            deleted_files, deleted_bytes, _, failures = self.helper['delete_stale_files'](self.root, time.time())
        self.assertEqual((deleted_files, deleted_bytes), (2, 20))
        self.assertEqual(failures, [(stale[0], 'Permission denied')])
        self.assertTrue(os.path.exists(stale[0]))

    # 削除スクリプト（clean-hook-temp-files skill）

    def test_dry_run_lists_without_deleting(self):
        stale, kept = self.create_layout()
        result = self.run_clean('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        output = result.stdout.decode('utf-8')
        self.assertIn(f'Temporary directory: {self.root}', output)
        self.assertIn('Files not updated for more than 48 hours: 3 (30 B)', output)
        self.assertIn('  hook-states/edit-oscillation: 1', output)
        self.assertIn('  debug-logs/prompt-transformations/session: 1', output)
        self.assertTrue(all(os.path.exists(path) for path in stale + kept))

    def test_deletes_stale_files_and_directories(self):
        stale, kept = self.create_layout()
        result = self.run_clean()
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        # 空になった古いディレクトリ（セッションのログとその親）は削除し、分類のディレクトリ自体は残す。
        self.assertIn('Deleted 3 files (30 B) and 3 empty directories.', result.stdout.decode('utf-8'))
        self.assertFalse(any(os.path.exists(path) for path in stale))
        self.assertTrue(all(os.path.exists(path) for path in kept))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'debug-logs', 'prompt-transformations')))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'hook-states', 'debug-mode')))
        for directory in ('hook-states', 'debug-logs', os.path.join('hook-states', 'edit-oscillation')):
            self.assertTrue(os.path.isdir(os.path.join(self.root, directory)))
        # 削除後は警告の対象もなくなる。
        self.assert_silent(self.invoke_hook())

    def test_recent_empty_directory_is_kept(self):
        # hookが作成した直後の空ディレクトリを消すと、その後の書き込みが失敗するため残す。
        os.makedirs(os.path.join(self.root, 'hook-states', 'known-files'))
        result = self.run_clean()
        self.assertEqual(result.returncode, 0)
        self.assertIn('Deleted 0 files (0 B) and 0 empty directories.', result.stdout.decode('utf-8'))
        self.assertTrue(os.path.isdir(os.path.join(self.root, 'hook-states', 'known-files')))

    def test_missing_temp_root(self):
        for arguments in (('--dry-run',), ()):
            with self.subTest(arguments=arguments):
                result = self.run_clean(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
                self.assertFalse(os.path.exists(self.root))

    def test_size_format(self):
        format_size = runpy.run_path(clean_script_path)['format_size']
        for size, expected in ((0, '0 B'), (1023, '1023 B'), (1024, '1.0 KB'), (1536, '1.5 KB'),
                               (1024 * 1024, '1.0 MB'), (5 * 1024 ** 3, '5120.0 MB')):
            with self.subTest(size=size):
                self.assertEqual(format_size(size), expected)


if __name__ == '__main__':
    run_tests()
