import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-commit-message-mismatch.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


_DENY_REASON_PREFIX = 'Commit denied: the commit message describes changes that are not in the commit'


class BlockCommitMessageMismatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-commit-message-mismatch-test-')
        self.addCleanup(temporary.cleanup)
        # hookの状態を実際の一時フォルダーに残さないよう、一時フォルダーを差し替える。
        self.__environment = dict(os.environ, TMPDIR=temporary.name, TEMP=temporary.name, TMP=temporary.name)
        self.__root = os.path.join(temporary.name, 'workspace')
        os.makedirs(self.__root)
        self.__git('init', '-q')
        self.__write('src/app.py', 'def parse():\n    return 1\n')
        self.__git('add', '-A')
        self.__git('commit', '-q', '-m', 'initial')

    def __git(self, *arguments):
        subprocess.run(
            ['git', '-c', 'user.name=test', '-c', 'user.email=test@example.com', '-c', 'commit.gpgsign=false', *arguments],
            cwd=self.__root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def __write(self, relative_path, content):
        path = os.path.join(self.__root, *relative_path.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __stage(self, relative_path, content):
        self.__write(relative_path, content)
        self.__git('add', '--', relative_path)

    def __new_session(self):
        return str(uuid.uuid4())

    def __invoke(self, command=None, session_id=None, tool_name='bash', raw=None, cwd=None):
        if raw is None:
            raw = json.dumps({
                'sessionId': session_id or self.__new_session(),
                'cwd': cwd or self.__root,
                'toolName': tool_name,
                'toolArgs': {'command': command},
            }, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=30,
        )

    def __assert_allowed(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_denied(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny')
        reason = output['permissionDecisionReason']
        self.assertTrue(reason.startswith(_DENY_REASON_PREFIX))
        self.assertEqual(result.stderr.decode('utf-8').strip(), reason)
        for fragment in fragments:
            self.assertIn(fragment, reason)
        return reason

    def test_allows_accurate_messages(self):
        self.__stage('src/app.py', 'def parse(text):\n    return len(text)\n')
        self.__stage('tests/test_app.py', 'def test_parse():\n    assert True\n')
        messages = (
            '`parse` に引数を追加し、テストを追加した',
            'Update `src/app.py` and add unit tests for `parse()`',
            'app.py の `parse` を直した (#12)',
            '設定を整理した\n\nCo-Authored-By: Bot <noreply@example.com>\nSee: https://example.com/a/b.json',
            '`git diff --cached` で確認した',
            '`test-<name>.py` と `*.md` と `src/{a,b}.py` の規約に揃えた',
        )
        for message in messages:
            with self.subTest(message=message):
                self.__assert_allowed(self.__invoke(f"git commit -m '{message}'"))

    def test_denies_names_missing_from_diff(self):
        self.__stage('src/app.py', 'def parse(text):\n    return len(text)\n')
        reason = self.__assert_denied(
            self.__invoke("git commit -m '`parse` と `render` を直し、docs/guide.md を更新した'"),
            '- render', '- docs/guide.md',
        )
        self.assertNotIn('- parse', reason)
        self.assertNotIn('no changed path looks like a test file', reason)

    def test_denies_test_claim_without_test_files(self):
        self.__stage('src/app.py', 'def parse(text):\n    return len(text)\n')
        for message in ('parseを直し、テストを追加した', 'Fix parse and add tests'):
            with self.subTest(message=message):
                self.__assert_denied(self.__invoke(f"git commit -m '{message}'"), 'no changed path looks like a test file')
        self.__assert_allowed(self.__invoke("git commit -m 'parseを直した。テストは追加していない'"))

    def test_reads_message_forms(self):
        self.__stage('src/app.py', 'def parse(text):\n    return len(text)\n')
        # 未追跡のファイルとして差分に含めないよう、メッセージのファイルは `.git/` に置く。
        self.__write('.git/message.txt', '`render` を直した\n')
        commands = (
            "git commit -q -F - <<'EOF' && git push\n`render` を直した\nEOF\ngit log -1",
            'git commit -m "$(cat <<\'EOF\'\n`render` を直した\nEOF\n)"',
            'git commit -F .git/message.txt',
            'git commit --file=.git/message.txt',
            'git commit --message="`render` を直した"',
            'git commit -m"`render` を直した"',
            'git commit -m 概要 -m "`render` を直した"',
            f'cd {json.dumps(os.path.dirname(self.__root))} && git -C {json.dumps(os.path.basename(self.__root))} commit -m "`render`"',
            'git add -A; git commit -m "`render`"',
        )
        for command in commands:
            for tool_name in ('bash', 'powershell', 'functions.bash'):
                with self.subTest(command=command, tool_name=tool_name):
                    self.__assert_denied(self.__invoke(command, tool_name=tool_name), '- render')

    def test_includes_worktree_and_untracked_files_when_staging_in_same_command(self):
        self.__stage('README.md', 'x\n')
        self.__write('src/app.py', 'def render():\n    return 1\n')
        self.__write('src/new_module.py', 'VALUE = 1\n')
        message = '`render` と `VALUE` を src/new_module.py に追加した'
        self.__assert_denied(self.__invoke(f"git commit -m '{message}'"), '- render', '- VALUE')
        for command in (
            f"git add -A && git commit -m '{message}'",
            f"git stage . && git commit -m '{message}'",
        ):
            with self.subTest(command=command):
                self.__assert_allowed(self.__invoke(command))
        # `-a` やパス指定では、追跡中のファイルの変更だけがコミットに入る。
        for command in ("git commit -am '`render`'", "git commit -m '`render`' -- src", "git commit -m '`render`' -i src/app.py"):
            with self.subTest(command=command):
                self.__assert_allowed(self.__invoke(command))
        reason = self.__assert_denied(self.__invoke("git commit -am '`render` と `VALUE`'"), '- VALUE')
        self.assertNotIn('- render', reason)

    def test_amend_compares_with_parent_commit(self):
        self.__stage('src/app.py', 'def render():\n    return 1\n')
        self.__git('commit', '-q', '-m', 'render')
        self.__stage('README.md', 'x\n')
        self.__assert_allowed(self.__invoke("git commit --amend -m '`render` を追加した'"))
        self.__assert_denied(self.__invoke("git commit -m '`render` を追加した'"))

    def test_skips_when_message_is_not_written_or_nothing_is_staged(self):
        commands = (
            "git commit -m '`render`'",
            "git commit --amend --no-edit",
            "git commit -C HEAD",
            "git commit --fixup=HEAD",
            "git commit",
            "git commit -F missing.txt",
            "git commit -F -",
            "echo 'git commit -m `render`'",
            "git log -m '`render`'",
            "git commit -m 'unclosed",
        )
        for command in commands:
            with self.subTest(command=command):
                self.__assert_allowed(self.__invoke(command))
        self.__stage('src/app.py', 'x = 1\n')
        for command in commands[1:]:
            with self.subTest(command=command, staged=True):
                self.__assert_allowed(self.__invoke(command))

    def test_skips_other_tools_and_non_repositories(self):
        self.__stage('src/app.py', 'x = 1\n')
        self.__assert_allowed(self.__invoke("git commit -m '`render`'", tool_name='edit'))
        self.__assert_allowed(self.__invoke(None))
        outside = tempfile.TemporaryDirectory(prefix='ghc-commit-message-mismatch-outside-')
        self.addCleanup(outside.cleanup)
        self.__assert_allowed(self.__invoke("git commit -m '`render`'", cwd=outside.name))
        self.__assert_allowed(self.__invoke("cd missing && git commit -m '`render`'"))

    def test_denies_each_message_once_per_session(self):
        self.__stage('src/app.py', 'x = 1\n')
        session_id = self.__new_session()
        self.__assert_denied(self.__invoke("git commit -m '`render`'", session_id))
        self.__assert_allowed(self.__invoke("git commit -m '`render`'", session_id))
        self.__assert_denied(self.__invoke("git commit -m '`renderer`'", session_id))
        self.__assert_denied(self.__invoke("git commit -m '`render`'"))

    def test_lists_names_with_limit(self):
        self.__stage('src/app.py', 'x = 1\n')
        message = ' '.join(f'`name{index:02}`' for index in range(12))
        reason = self.__assert_denied(self.__invoke(f"git commit -m '{message}'"), '- name09', '... and 2 more')
        self.assertNotIn('name10', reason)

    def test_invalid_input_reports_error(self):
        valid = {'sessionId': str(uuid.uuid4()), 'cwd': self.__root, 'toolName': 'bash', 'toolArgs': {'command': 'ls'}}
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'\xff']
        for key, value in (
            ('sessionId', None), ('sessionId', ''), ('sessionId', 'not-a-uuid'),
            ('cwd', None), ('cwd', 'relative'), ('toolName', None), ('toolName', ' '),
        ):
            invalid.append(json.dumps({**valid, key: value}).encode('utf-8'))
        invalid.append(json.dumps({key: value for key, value in valid.items() if key != 'toolArgs'}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to check the commit message:', result.stderr)


if __name__ == '__main__':
    run_tests()
