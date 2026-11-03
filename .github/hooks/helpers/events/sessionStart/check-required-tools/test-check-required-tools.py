import contextlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'check-required-tools.py')
definitions_path = os.path.join(script_directory, 'required-tool-definitions.json')
hook = runpy.run_path(hook_path)

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

COMMANDS = ('ast-grep', 'git', 'jq', 'rg')
SATISFIED = {'ast-grep': 'ast-grep 0.39.5\n', 'git': 'git version 2.47.1\n', 'jq': 'jq-1.7.1\n', 'rg': 'ripgrep 14.1.1\n'}
# 条件を満たさない場合の、コマンドごとの案内。
REQUIREMENTS = {
    'ast-grep': 'ast-grep is required, and it must be available as the ast-grep command.',
    'git': 'Git 2.34.1 or later is required, and it must be available as the git command.',
    'jq': 'jq 1.6 or later is required, and it must be available as the jq command.',
    'rg': 'ripgrep 13.0.0 or later is required, and it must be available as the rg command.',
}
INSTALL_MESSAGES = {
    'win32': {
        'ast-grep': 'Install it with: winget install --id ast-grep.ast-grep --exact --source winget',
        'git': 'Install it with: winget install --id Git.Git --exact --source winget',
        'jq': 'Install it with: winget install --id jqlang.jq --exact --source winget',
        'rg': 'Install it with: winget install --id BurntSushi.ripgrep.MSVC --exact --source winget',
    },
    'darwin': {
        'ast-grep': 'Install it with: brew install ast-grep',
        'git': 'Install it with: brew install git',
        'jq': 'Install it with: brew install jq',
        'rg': 'Install it with: brew install ripgrep',
    },
    'linux': {
        'ast-grep': 'Installation guide: https://github.com/ast-grep/ast-grep#installation',
        'git': 'Installation guide: https://git-scm.com/install/linux',
        'jq': 'Installation guide: https://github.com/jqlang/jq#installation',
        'rg': 'Installation guide: https://github.com/BurntSushi/ripgrep#installation',
    },
}
RESTART = 'After installation, restart the application or terminal running this hook to refresh PATH.'


def run_main(platform='linux', results=None, arguments=()):
    # resultsはコマンドごとの `--version` の標準出力、または起動時に送出する例外。指定のないコマンドは条件を満たす。
    results = {**SATISFIED, **(results or {})}

    def fake_run(command, **_):
        result = results[command[0]]
        if isinstance(result, BaseException):
            raise result
        return Mock(stdout=result)

    run = Mock(side_effect=fake_run)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        patch.object(hook['subprocess'], 'run', run),
        patch.object(hook['sys'], 'platform', platform),
        patch.object(stdout, 'reconfigure', create=True),
        patch.object(stderr, 'reconfigure', create=True),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        exit_code = hook['main'](list(arguments))
    return exit_code, stderr.getvalue(), stdout.getvalue(), run


def expected_message(platform, commands):
    lines = [line for command in commands for line in (REQUIREMENTS[command], INSTALL_MESSAGES[platform][command])]
    return '\n'.join([*lines, RESTART])


class CheckRequiredToolsTests(unittest.TestCase):
    def assert_notified(self, result, platform, commands):
        # 案内を標準エラーに出し、同じ案内をadditionalContextでエージェントに渡して正常終了すること。
        exit_code, stderr, stdout, _ = result
        expected = expected_message(platform, commands)
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, f'{expected}\n')
        self.assertEqual(json.loads(stdout), {'additionalContext': hook['ADDITIONAL_CONTEXT'].format(message=expected)})

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'sessionStart.json'), encoding='utf-8') as file:
            entries = json.load(file)['hooks']['sessionStart']
        relative_path = '.github/hooks/helpers/events/sessionStart/check-required-tools/check-required-tools.py'
        matching = [entry for entry in entries if relative_path in entry.get('bash', '')]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')

    def test_all_satisfied(self):
        exit_code, stderr, stdout, run = run_main()
        self.assertEqual((exit_code, stderr, stdout), (0, '', ''))
        self.assertEqual(
            [call.args[0] for call in run.call_args_list], [[command, '--version'] for command in COMMANDS],
        )
        run.assert_called_with(['rg', '--version'], capture_output=True, text=True, errors='replace', check=True)

    def test_supported_versions(self):
        for command, outputs in {
            'ast-grep': ['ast-grep 0.1.0\n', 'ast-grep 1.0.0\n'],
            'git': ['git version 2.34.1\n', 'git version 2.47.1.windows.2\n',
                    'git version 2.39.5 (Apple Git-154)\n', 'git version 3.0.0\n'],
            # jqはパッチ番号を省略する版があるため、省略時は0とみなす。
            'jq': ['jq-1.6\n', 'jq-1.8.0-dirty\n', 'jq-2.0\n'],
            'rg': ['ripgrep 13.0.0\n-SIMD -AVX (compiled)\n', 'ripgrep 14.1.1 (rev 4649aa9700)\n\nfeatures:+pcre2\n',
                   'ripgrep 15.0.0 (rev 3a612f88b8)\n'],
        }.items():
            for output in outputs:
                with self.subTest(command=command, output=output):
                    self.assertEqual(run_main(results={command: output})[:3], (0, '', ''))

    def test_unsatisfied_cases(self):
        for command, output in (
            ('git', 'git version 2.34.0\n'), ('jq', 'jq-1.5-1-a5b5cbe\n'), ('rg', 'ripgrep 12.1.1\n'),
            ('ast-grep', 'unknown\n'), ('ast-grep', ''), ('git', 'unknown\n'),
            ('jq', FileNotFoundError()), ('rg', subprocess.CalledProcessError(1, ['rg', '--version'])),
        ):
            with self.subTest(command=command, output=output):
                self.assert_notified(run_main(results={command: output}), 'linux', [command])

    def test_installation_messages(self):
        # 足りないツールの案内は定義の順に1つにまとめ、再起動の案内は最後に1回だけ付ける。
        missing = {command: FileNotFoundError() for command in COMMANDS}
        for platform in INSTALL_MESSAGES:
            with self.subTest(platform=platform):
                self.assert_notified(run_main(platform, missing), platform, COMMANDS)
        self.assert_notified(run_main(results={'git': FileNotFoundError(), 'rg': 'old\n'}), 'linux', ['git', 'rg'])

    def test_invalid_definitions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'definitions.json')
            with open(definitions_path, encoding='utf-8') as file:
                valid_tool = json.load(file)['tools'][0]
            for name, content in (
                ('broken json', '{'),
                ('not an object', '[]'),
                ('empty tools', '{"tools":[]}'),
                ('missing command', json.dumps({'tools': [{**valid_tool, 'command': ''}]})),
                ('invalid pattern', json.dumps({'tools': [{**valid_tool, 'versionPattern': '('}]})),
                ('invalid minimum version', json.dumps({'tools': [{**valid_tool, 'minimumVersion': '1.x'}]})),
                ('missing platform', json.dumps({'tools': [{**valid_tool, 'install': {'win32': 'a', 'darwin': 'b'}}]})),
            ):
                with self.subTest(name=name):
                    with open(path, 'w', encoding='utf-8') as file:
                        file.write(content)
                    exit_code, stderr, stdout, run = run_main(arguments=('--definitions-path', path))
                    self.assertEqual((exit_code, stdout), (2, ''))
                    self.assertIn('Failed to load required tool definitions', stderr)
                    run.assert_not_called()

    def test_missing_command_process(self):
        with tempfile.TemporaryDirectory() as empty_directory:
            environment = {**os.environ, 'PATH': empty_directory}
            # WindowsはPATHのほかにSystem32なども探すため、そこにあるツールは見つからない状態を作れない。
            missing = []
            for command in COMMANDS:
                try:
                    subprocess.run([command, '--version'], capture_output=True, cwd=empty_directory, env=environment)
                except OSError:
                    missing.append(command)
            if not missing:
                self.skipTest('All tools are found outside PATH, for example in System32.')
            result = subprocess.run(
                [sys.executable, hook_path], input=b'{"sessionId":"test"}', capture_output=True,
                cwd=empty_directory, env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for name, output in (('stdout', result.stdout), ('stderr', result.stderr)):
                self.assertFalse(output.startswith(b'\xef\xbb\xbf'), name)
                for command in missing:
                    self.assertIn(REQUIREMENTS[command].encode('utf-8'), output, name)
            self.assertIn('additionalContext', json.loads(result.stdout.decode('utf-8')))


if __name__ == '__main__':
    run_tests()
