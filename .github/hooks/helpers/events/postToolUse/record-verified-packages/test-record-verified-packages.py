import json
import os
import runpy
import subprocess
import sys
import unittest
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'record-verified-packages.py')

_helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_hooks_directory = os.path.dirname(_helpers_directory)
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_state = runpy.run_path(os.path.join(_helpers_directory, 'utilities', 'verified-packages-state', 'verified-packages-state.py'))


def _run_hook(input_bytes):
    return subprocess.run([sys.executable, _hook_path], input=input_bytes, capture_output=True)


class RecordVerifiedPackagesTests(unittest.TestCase):
    def setUp(self):
        self.new_session()

    def new_session(self):
        # テストごとに別セッションとし、確認の記録を持ち越さない。
        self.session_id = str(uuid.uuid4())
        self.state_path = _state['get_state_path'](self.session_id)
        self.addCleanup(lambda path=self.state_path: os.path.exists(path) and os.remove(path))

    def run_call(self, tool_name, tool_args, output):
        hook_input = {
            'sessionId': self.session_id, 'cwd': os.getcwd(), 'toolName': tool_name, 'toolArgs': tool_args,
            'toolResult': {'resultType': 'success', 'textResultForLlm': output},
        }
        result = _run_hook(json.dumps(hook_input).encode('utf-8'))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))
        return _state['get_verified'](self.state_path)

    def test_registry_queries_are_recorded(self):
        cases = [
            ('dotnet package search Newtonsoft.Json', '| Newtonsoft.Json | 13.0.3 | 5B |', ('nuget', 'newtonsoft.json')),
            ('dotnet package search --take 5 --exact Serilog', 'Serilog 4.0.0', ('nuget', 'serilog')),
            ('dotnet add App/App.csproj package Polly --version 8.4.0', "info : PackageReference for package 'Polly'", ('nuget', 'polly')),
            ('dotnet add package Polly', "PackageReference for package 'Polly' added", ('nuget', 'polly')),
            ('nuget list Dapper', 'Dapper 2.1.35', ('nuget', 'dapper')),
            ('pip index versions requests', 'requests (2.32.3)\nAvailable versions: 2.32.3', ('pypi', 'requests')),
            ('python -m pip index versions PyYAML', 'PyYAML (6.0.2)', ('pypi', 'pyyaml')),
            ('pip download --no-deps Fake_HTTP==1.0', 'Saved ./fake_http-1.0.whl', ('pypi', 'fake-http')),
            ('uv add httpx', 'Resolved 5 packages\n + httpx==0.27.0', ('pypi', 'httpx')),
            ('npm view left-pad version', '1.3.0 left-pad', ('npm', 'left-pad')),
            ('npm info @scope/ui@1.2.0', '@scope/ui@1.2.0 | MIT', ('npm', '@scope/ui')),
            ('pnpm add lodash', 'dependencies:\n+ lodash 4.17.21', ('npm', 'lodash')),
            ('python3 -c "import yaml"', '', ('python-module', 'yaml')),
            ('cd web && npm view react version', 'react 18.3.1', ('npm', 'react')),
        ]
        for command, output, expected in cases:
            with self.subTest(command=command):
                self.new_session()
                self.assertEqual(self.run_call('bash', {'command': command}, output), {expected}, command)

    def test_failed_queries_are_not_recorded(self):
        cases = [
            ('dotnet package search Contoso.Missing', 'No results found.'),
            ('dotnet add package Contoso.Missing', 'error: NU1101: Unable to find package Contoso.Missing.'),
            ('pip index versions zz-missing', 'ERROR: No matching distribution found for zz-missing'),
            ('npm view zz-missing', 'npm ERR! code E404\nnpm ERR! 404 Not Found'),
            ('uv add zz-missing', 'No solution found when resolving dependencies'),
            ('python -c "import zz_missing"', "ModuleNotFoundError: No module named 'zz_missing'"),
            # 出力に名前がない。
            ('npm view left-pad', ''),
            # インストールや無関係なコマンドは確認とみなさない。
            ('pip install requests', 'Successfully installed requests-2.32.3'),
            ('npm install left-pad', 'added 1 package: left-pad'),
            ('echo dotnet package search', 'dotnet package search'),
        ]
        for command, output in cases:
            with self.subTest(command=command):
                self.new_session()
                self.assertEqual(self.run_call('powershell', {'command': command}, output), set(), command)

    def test_registry_pages_are_recorded(self):
        cases = [
            ('https://www.nuget.org/packages/Serilog/4.0.0', ('nuget', 'serilog')),
            ('https://api.nuget.org/v3-flatcontainer/polly/index.json', ('nuget', 'polly')),
            ('https://api.nuget.org/v3/registration5-gz-semver2/dapper/index.json', ('nuget', 'dapper')),
            ('https://pypi.org/project/requests/', ('pypi', 'requests')),
            ('https://pypi.org/pypi/PyYAML/json', ('pypi', 'pyyaml')),
            ('https://www.npmjs.com/package/@scope/ui', ('npm', '@scope/ui')),
            ('https://registry.npmjs.org/left-pad', ('npm', 'left-pad')),
            ('https://registry.npmjs.org/@scope%2Fui', ('npm', '@scope/ui')),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.new_session()
                self.assertEqual(self.run_call('web_fetch', {'url': url}, 'Package page content'), {expected}, url)

    def test_missing_registry_pages_are_not_recorded(self):
        cases = [
            ('https://www.nuget.org/packages/Contoso.Missing', '404 - Page not found'),
            ('https://pypi.org/project/zz-missing/', 'Not Found'),
            ('https://registry.npmjs.org/zz-missing', ''),
            ('https://example.com/packages/left-pad', 'left-pad'),
        ]
        for url, output in cases:
            with self.subTest(url=url):
                self.new_session()
                self.assertEqual(self.run_call('functions.web_fetch', {'url': url}, output), set(), url)

    def test_other_tools_are_skipped(self):
        self.assertEqual(self.run_call('view', {'path': 'requirements.txt'}, 'requests'), set())
        self.assertEqual(self.run_call('bash', 'not json', 'requests'), set())
        self.assertFalse(os.path.exists(self.state_path))

    def test_invalid_input_fails(self):
        inputs = [
            b'',
            b'[]',
            json.dumps({'sessionId': 'x', 'toolName': 'bash', 'toolArgs': {}}).encode(),
            json.dumps({'sessionId': self.session_id, 'toolArgs': {}}).encode(),
            json.dumps({'sessionId': self.session_id, 'toolName': 'bash'}).encode(),
        ]
        for input_bytes in inputs:
            with self.subTest(input_bytes=input_bytes):
                result = _run_hook(input_bytes)
                self.assertEqual(result.returncode, 2, input_bytes)
                self.assertIn(b'Failed to record verified packages', result.stderr)


if __name__ == '__main__':
    run_tests()
