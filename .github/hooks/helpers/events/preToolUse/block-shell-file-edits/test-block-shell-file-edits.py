import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-shell-file-edits.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_get_hook_entry = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-entry.py'))['get_hook_entry']


class BlockShellFileEditsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-shell-file-edits-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__workspace = os.path.join(self.__root, 'workspace')
        os.makedirs(os.path.join(self.__workspace, 'sub'))
        for path in ('対象.txt', 'other.txt', 'untracked.txt', os.path.join('sub', 'nested.txt')):
            with open(os.path.join(self.__workspace, path), 'w', encoding='utf-8', newline='\n') as file:
                file.write('a\n')
        subprocess.run(['git', 'init', '-q', self.__workspace], check=True, timeout=30)
        subprocess.run(
            ['git', '-C', self.__workspace, 'add', '--', '対象.txt', 'other.txt', 'sub/nested.txt'], check=True, timeout=30,
        )
        self.__outside = os.path.join(self.__root, 'outside.txt')
        with open(self.__outside, 'w', encoding='utf-8', newline='\n') as file:
            file.write('a\n')

    def __payload(self, tool_call):
        return {
            'sessionId': 'test-session',
            'cwd': self.__workspace,
            'toolName': tool_call[0],
            'toolArgs': tool_call[1],
        }

    def __run_hook(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )

    def __invoke(self, tool_call):
        return self.__run_hook(self.__payload(tool_call))

    def __assert_allowed(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_denied(self, result, files):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'permissionDecision', 'permissionDecisionReason'})
        self.assertEqual(output['permissionDecision'], 'deny')
        self.assertIn(f'writes to the following git-tracked files: {files}.', output['permissionDecisionReason'])

    def test_configuration(self):
        entry = _get_hook_entry(_hook_path)
        for tool_name in ('bash', 'powershell', 'functions.bash', 'functions.powershell'):
            with self.subTest(tool_name=tool_name):
                self.assertRegex(tool_name, f'^(?:{entry["matcher"]})$')
        for tool_name in ('edit', 'view', 'apply_patch'):
            with self.subTest(tool_name=tool_name):
                self.assertNotRegex(tool_name, f'^(?:{entry["matcher"]})$')

    def test_denies_bash_writes_to_tracked_files(self):
        commands = (
            "sed -i 's/a/b/' 対象.txt",
            'sed -i.bak -e s/a/b/ 対象.txt',
            "sed -i '' 's/a/b/' 対象.txt",
            'sed --in-place=.bak s/a/b/ 対象.txt',
            'sed -Ei s/a/b/ ./sub/../対象.txt',
            "perl -pi -e 's/a/b/' 対象.txt",
            'echo x > 対象.txt',
            'echo x>>対象.txt',
            'make 2> 対象.txt',
            'make &>対象.txt',
            'make >| 対象.txt',
            'printf x | tee -a 対象.txt',
            'FOO=1 sudo -n sed -i s/a/b/ 対象.txt',
            '/usr/bin/sed -i s/a/b/ 対象.txt',
            'sed -i s/a/b/ 対象.*',
            "cat > 対象.txt <<'EOF'\nbody\nEOF",
            'echo hi # comment\nsed -i s/a/b/ 対象.txt',
            'echo "multi\nline" \\\n  > 対象.txt',
            f"echo x > '{os.path.join(self.__workspace, '対象.txt')}'",
        )
        for command in commands:
            with self.subTest(command=command):
                self.__assert_denied(self.__invoke(('bash', {'command': command})), '対象.txt')

    def test_denies_powershell_writes_to_tracked_files(self):
        commands = (
            'Set-Content -Path 対象.txt -Value x',
            "'x' | Out-File 対象.txt -Encoding utf8",
            'Add-Content 対象.txt x',
            'sc 対象.txt x',
            "set-content -LiteralPath:'対象.txt' x",
            'echo x > 対象.txt',
            'echo x 2>&1 >> "対象.txt"',
            "[System.IO.File]::WriteAllText('対象.txt', 'x')",
            '[IO.File]::AppendAllLines("対象.txt", $lines)',
            'Get-Content untracked.txt | Set-Content `\n  対象.txt',
            'Clear-Content 対象.txt',
            "Out-File -FilePath untracked.txt,対象.txt -InputObject x",
            "sed -i 's/a/b/' 対象.txt",
            # PowerShellのteeはTee-Objectの別名で、最初の引数に書き込む。
            'Get-Date | tee 対象.txt',
        )
        for command in commands:
            with self.subTest(command=command):
                self.__assert_denied(self.__invoke(('powershell', {'command': command})), '対象.txt')

    @unittest.skipUnless(os.name == 'nt', 'powershellツールはWindowsで使われ、`\\` をパスの区切りとするのはWindowsだけのため。')
    def test_denies_powershell_writes_with_backslash_paths(self):
        self.__assert_denied(self.__invoke(('powershell', {'command': 'Clear-Content .\\対象.txt'})), '対象.txt')

    def test_follows_directory_changes(self):
        # 理由のパスは、OSによらず `/` 区切りで示される。
        nested = 'sub/nested.txt'
        cases = (
            ('bash', 'cd sub && echo x > nested.txt'),
            ('bash', 'cd sub; cd ..; echo x > sub/nested.txt'),
            ('powershell', "Set-Location sub; 'x' | Tee-Object -FilePath nested.txt"),
        )
        for tool_name, command in cases:
            with self.subTest(command=command):
                self.__assert_denied(self.__invoke((tool_name, {'command': command})), nested)
        # 移動先がわからない場合は基準を移さない。
        self.__assert_allowed(self.__invoke(('bash', {'command': 'cd "$DIR" && echo x > nested.txt'})))

    def test_lists_multiple_files_and_tool_formats(self):
        self.__assert_denied(self.__invoke(('functions.bash', json.dumps({'command': 'echo x > other.txt'}))), 'other.txt')
        self.__assert_allowed(self.__invoke(('view', {'path': '対象.txt'})))
        result = self.__invoke(('PowerShell', {'command': "Set-Content 対象.txt x; Set-Content 'other.txt' y"}))
        self.__assert_denied(result, ', '.join(sorted(['other.txt', '対象.txt'])))
        result = self.__invoke(('bash', {'command': 'echo x > other.txt; echo x | tee sub/nested.txt untracked.txt'}))
        self.__assert_denied(result, ', '.join(sorted(['other.txt', 'sub/nested.txt'])))

    def test_allows_commands_that_do_not_write_tracked_files(self):
        cases = (
            ('bash', 'echo x > untracked.txt'),
            ('bash', 'echo x > new.txt'),
            ('bash', f"echo x > '{self.__outside}'"),
            ('bash', 'make > /dev/null 2>&1'),
            ('bash', 'make 2>&1 | tail'),
            ('bash', 'cat 対象.txt; grep a < 対象.txt'),
            ('bash', "sed 's/a/b/' 対象.txt"),
            ('bash', "sed -n 's/a/b/p' 対象.txt > untracked.txt"),
            ('bash', 'sed -i s/a/b/ untracked.txt'),
            ('bash', 'echo "a > 対象.txt"'),
            ('bash', "cat <<EOF > untracked.txt\n> 対象.txt\nEOF"),
            ('bash', 'cat <<< "x" > untracked.txt'),
            ('bash', 'echo x > "$TARGET"'),
            ('bash', 'echo x > `name`'),
            ('bash', "echo 'unclosed > 対象.txt"),
            ('bash', 'tee'),
            ('bash', 'Set-Content 対象.txt x'),
            ('powershell', 'Get-Content 対象.txt'),
            ('powershell', 'Write-Output x > $null'),
            ('powershell', "Write-Output 'a > 対象.txt'"),
            ('powershell', '# echo x > 対象.txt\nGet-Date'),
            ('powershell', '<# echo x > 対象.txt #> Get-Date'),
            ("powershell", "$s = @'\necho x > 対象.txt\n'@\nSet-Content untracked.txt $s"),
            ('powershell', 'Write-Output 1 2>&1'),
            ('powershell', 'Set-Content $path x'),
        )
        for tool_name, command in cases:
            with self.subTest(command=command):
                self.__assert_allowed(self.__invoke((tool_name, {'command': command})))

    def test_allows_other_tools_and_missing_commands(self):
        cases = (
            ('edit', {'path': '対象.txt'}),
            ('bash', {}),
            ('bash', None),
            ('bash', 'not json'),
            ('bash', {'command': 3}),
        )
        for tool_call in cases:
            with self.subTest(tool_call=tool_call):
                self.__assert_allowed(self.__invoke(tool_call))

    def test_allows_outside_git_repository(self):
        payload = self.__payload(('bash', {'command': 'echo x > outside.txt'}))
        payload['cwd'] = self.__root
        self.__assert_allowed(self.__run_hook(payload))

    def test_invalid_input_reports_error(self):
        base = self.__payload(('bash', {'command': 'echo x'}))
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'true', b'{"cwd":"\xff"}']
        for field in ('cwd', 'toolName', 'toolArgs'):
            payload = base.copy()
            del payload[field]
            invalid.append(json.dumps(payload).encode('utf-8'))
        for value in ('', ' \t', None, 3, False, [], {}, 'relative'):
            invalid.append(json.dumps({**base, 'cwd': value}).encode('utf-8'))
        for value in (None, '', ' \t', 3, [], {}):
            invalid.append(json.dumps({**base, 'toolName': value}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__run_hook(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to check shell file edits:', result.stderr)


if __name__ == '__main__':
    _run_tests()
