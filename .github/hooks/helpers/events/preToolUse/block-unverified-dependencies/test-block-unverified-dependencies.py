import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-unverified-dependencies.py')

_helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_hooks_directory = os.path.dirname(_helpers_directory)
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_state = runpy.run_path(os.path.join(_helpers_directory, 'utilities', 'verified-packages-state', 'verified-packages-state.py'))

_CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk">\n'
    '  <ItemGroup>\n'
    '    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />\n'
    '  </ItemGroup>\n'
    '</Project>\n'
)


class BlockUnverifiedDependenciesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-unverified-dependencies-test-')
        self.addCleanup(temporary.cleanup)
        root = os.path.realpath(temporary.name)
        # 確認の記録を実際の一時フォルダーに残さないよう、hookとテストの両方で一時フォルダーを差し替える。
        self.__environment = dict(os.environ, TMPDIR=root, TEMP=root, TMP=root)
        tempdir = patch.object(tempfile, 'tempdir', root)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.__directory = os.path.join(root, 'workspace')
        os.makedirs(self.__directory)
        if shutil.which('git') is not None:
            subprocess.run(['git', 'init', '-q', self.__directory], check=True, timeout=30)
        # 実際のNuGetのキャッシュに左右されないよう、空の一時フォルダーを使う。
        self.__nuget_directory = os.path.join(self.__directory, '.nuget-packages')
        os.makedirs(os.path.join(self.__nuget_directory, 'newtonsoft.json', '13.0.3'))
        os.makedirs(os.path.join(self.__nuget_directory, 'serilog', '4.0.0'))
        self.__write_file('.gitignore', '.nuget-packages/\n.venv/\nnode_modules/\n')
        self.__write_file('App/App.csproj', _CSPROJ)
        self.__new_session()

    def __new_session(self):
        # テストごとに別セッションとし、確認の記録を持ち越さない。
        self.__session_id = str(uuid.uuid4())
        self.__state_path = _state['get_state_path'](self.__session_id)

    def __write_file(self, path, content):
        full_path = os.path.join(self.__directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __run_raw(self, input_bytes):
        environment = dict(self.__environment, NUGET_PACKAGES=self.__nuget_directory)
        return subprocess.run([sys.executable, _hook_path], input=input_bytes, capture_output=True, env=environment, timeout=30)

    def __run_call(self, tool_name, tool_args):
        hook_input = {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': tool_name, 'toolArgs': tool_args}
        return self.__run_raw(json.dumps(hook_input).encode('utf-8'))

    def __create(self, path, content):
        return self.__run_call('create', {'path': path, 'file_text': content})

    def __assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)
        return output['permissionDecisionReason']

    def __add_reference(self, include, version='1.0.0'):
        return self.__run_call('edit', {
            'path': 'App/App.csproj',
            'old_str': '  </ItemGroup>',
            'new_str': f'    <PackageReference Include="{include}" Version="{version}" />\n  </ItemGroup>',
        })

    def test_unknown_nuget_package_is_denied(self):
        reason = self.__assert_denied(
            self.__add_reference('Newtonsfot.Json'),
            ['NuGet package `Newtonsfot.Json`', 'did you mean `newtonsoft.json`?', 'dotnet package search'],
            'unknown package',
        )
        self.assertIn('Do not install', reason)

    def test_cached_nuget_package_is_allowed(self):
        self.__assert_allowed(self.__add_reference('Serilog', '4.0.0'), 'cached package')
        self.__assert_allowed(self.__add_reference('serilog', '4.0'), 'cached package with a short version')

    def test_unknown_nuget_version_is_denied(self):
        self.__assert_denied(
            self.__add_reference('Serilog', '9.9.9'), ['NuGet package `Serilog` version `9.9.9` (not in the local package cache)'],
            'unknown version',
        )

    def test_floating_nuget_versions_are_not_checked(self):
        for version in ('4.*', '[4.0,5.0)', '$(SerilogVersion)'):
            with self.subTest(version=version):
                self.__assert_allowed(self.__add_reference('Serilog', version), version)

    def test_package_in_other_manifest_is_allowed(self):
        self.__write_file('Lib/Lib.csproj', _CSPROJ.replace('Newtonsoft.Json" Version="13.0.3', 'Contoso.Internal" Version="2.0.0'))
        self.__assert_allowed(self.__add_reference('Contoso.Internal', '2.0.0'), 'other manifest')

    def test_restored_assets_are_allowed(self):
        self.__write_file('App/obj/project.assets.json', json.dumps({'libraries': {'Polly/8.4.0': {}}}))
        self.__assert_allowed(self.__add_reference('Polly', '8.4.0'), 'restored assets')

    def test_existing_reference_is_allowed(self):
        self.__assert_allowed(self.__run_call('edit', {
            'path': 'App/App.csproj', 'old_str': '  <ItemGroup>', 'new_str': '  <ItemGroup Label="Packages">',
        }), 'unrelated edit')
        moved = _CSPROJ.replace('Newtonsoft.Json" Version="13.0.3" /', 'Newtonsoft.Json" Version="13.0.3" PrivateAssets="all" /')
        self.__write_file('Other/Other.csproj', '<Project />\n')
        self.__assert_allowed(self.__create('Other/Other.csproj', moved), 'reference in other manifest')

    def test_verified_package_is_allowed(self):
        self.__assert_denied(self.__add_reference('Contoso.Missing'), ['Contoso.Missing'], 'before verification')
        self.__assert_denied(self.__add_reference('Contoso.Missing'), ['Contoso.Missing'], 'denied again')
        _state['record_verified'](self.__state_path, [('nuget', 'contoso.missing')])
        self.__assert_allowed(self.__add_reference('Contoso.Missing', '9.9.9'), 'after verification')

    def test_requirements_are_checked(self):
        site_packages = os.path.join(self.__directory, '.venv', 'lib', 'python3.14', 'site-packages')
        os.makedirs(os.path.join(site_packages, 'fake_http-1.0.0.dist-info'))
        self.__assert_allowed(self.__create('requirements.txt', 'fake-http==1.0.0\n-r base.txt\n# comment\n'), 'installed in .venv')
        self.__write_file('uv.lock', '[[package]]\nname = "locked-lib"\nversion = "1.0"\n')
        self.__assert_allowed(self.__create('requirements-dev.txt', 'Locked_Lib>=1\n'), 'locked')
        self.__assert_denied(
            self.__create('requirements.txt', 'fake-http==1.0.0\nfake-httpx>=2\n'),
            ['PyPI package `fake-httpx`', 'did you mean `fake-http`?'], 'unknown requirement',
        )

    def test_pyproject_is_checked(self):
        cases = [
            '[project]\ndependencies = [\n    "zz-unknown-lib>=1.0",\n]\n',
            '[project]\ndependencies = ["zz-unknown-lib"]\n',
            '[tool.poetry.dependencies]\nzz-unknown-lib = "^1.2"\n',
        ]
        for content in cases:
            with self.subTest(content=content):
                self.__assert_denied(self.__create('pyproject.toml', content), ['PyPI package `zz-unknown-lib`'], content)
        self.__assert_allowed(self.__create('pyproject.toml', (
            '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.14"\n'
            'keywords = ["zz-unknown-lib"]\n\n[tool.poetry.dependencies]\npython = "^3.14"\n'
            '[tool.pytest.ini_options]\nminversion = "8.0"\n'
        )), 'non-dependency keys')

    def test_package_json_is_checked(self):
        os.makedirs(os.path.join(self.__directory, 'web', 'node_modules', '@scope', 'ui'))
        os.makedirs(os.path.join(self.__directory, 'web', 'node_modules', 'left-pad'))
        self.__write_file('web/package-lock.json', '{"packages": {"node_modules/locked-lib": {}}}\n')
        self.__assert_allowed(self.__create('web/package.json', (
            '{\n  "name": "web",\n  "version": "1.0.0",\n  "engines": {\n    "node": ">=20"\n  },\n'
            '  "scripts": {\n    "build": "tsc"\n  },\n  "dependencies": {\n    "@scope/ui": "^1.0.0",\n'
            '    "left-pad": "1.3.0",\n    "locked-lib": "~2.0.0"\n  }\n}\n'
        )), 'installed, locked, and non-dependency entries')
        self.__assert_denied(
            self.__create('web/package.json', '{\n  "dependencies": {\n    "left-pads": "^1.3.0"\n  }\n}\n'),
            ['npm package `left-pads`', 'did you mean `left-pad`?'], 'unknown npm package',
        )

    def test_python_imports_are_checked(self):
        # setUpの `App/` と、大文字小文字を区別しないファイルシステムで衝突しない名前にする。
        self.__write_file('pkg/helpers.py', '')
        self.__assert_allowed(self.__create('pkg/main.py', (
            'import os, sys as system\nfrom collections import abc\nfrom __future__ import annotations\n'
            'import helpers\nfrom pkg import helpers\nfrom . import helpers\nimport json  # comment\n'
        )), 'stdlib and local modules')
        self.__assert_denied(
            self.__create('pkg/main.py', 'import jsonx\nfrom zz_missing_module.sub import x\n'),
            ['Python module `jsonx` (did you mean `json`?)', 'Python module `zz_missing_module`', 'python -c "import <name>"'],
            'unknown modules',
        )
        _state['record_verified'](self.__state_path, [('python-module', 'jsonx'), ('python-module', 'zz_missing_module')])
        self.__assert_allowed(self.__create('pkg/main.py', 'import jsonx\nfrom zz_missing_module.sub import x\n'), 'verified modules')

    def test_python_module_in_venv_is_allowed(self):
        site_packages = os.path.join(self.__directory, '.venv', 'lib', 'python3.14', 'site-packages')
        os.makedirs(os.path.join(site_packages, 'zz_venv_only'))
        self.__assert_allowed(self.__create('main.py', 'import zz_venv_only\n'), 'module in .venv')

    def test_apply_patch_is_checked(self):
        patch = (
            '*** Begin Patch\n*** Update File: App/App.csproj\n@@\n'
            '+    <PackageReference Include="Contoso.Missing" Version="1.0.0" />\n   </ItemGroup>\n*** End Patch\n'
        )
        self.__assert_denied(self.__run_call('apply_patch', patch), ['Contoso.Missing'], 'patch')

    def test_findings_are_limited(self):
        content = ''.join(f'zz-missing-{index}\n' for index in range(12))
        self.__assert_denied(self.__create('requirements.txt', content), ['and 2 more'], 'limit')

    def test_other_files_and_tools_are_skipped(self):
        self.__assert_allowed(self.__create('README.md', 'import zz_missing\n'), 'markdown')
        self.__assert_allowed(self.__create('src/app.ts', "import x from 'zz-missing';\n"), 'TypeScript source')
        self.__assert_allowed(self.__run_call('bash', {'command': 'echo zz-missing >> requirements.txt'}), 'bash')
        self.__assert_allowed(self.__run_call('str_replace_editor', {'command': 'view', 'path': 'App/App.csproj'}), 'view')

    def test_invalid_input_fails(self):
        inputs = [
            b'',
            b'[]',
            json.dumps({'sessionId': 'x', 'cwd': self.__directory, 'toolName': 'create', 'toolArgs': {}}).encode(),
            json.dumps({'sessionId': self.__session_id, 'cwd': 'relative', 'toolName': 'create', 'toolArgs': {}}).encode(),
            json.dumps({'sessionId': self.__session_id, 'cwd': self.__directory, 'toolArgs': {}}).encode(),
            json.dumps({'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'create'}).encode(),
        ]
        for input_bytes in inputs:
            with self.subTest(input_bytes=input_bytes):
                result = self.__run_raw(input_bytes)
                self.assertEqual(result.returncode, 2, input_bytes)
                self.assertIn(b'Failed to check unverified dependencies', result.stderr)


if __name__ == '__main__':
    _run_tests()
