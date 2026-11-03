import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'detect-lockfile-drift.py')
definitions_path = os.path.join(script_directory, 'lockfile-drift-definitions.json')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


HOOK_TIMESTAMP = 1777777777000
REASON_PREFIX = 'The changes in this request edit dependency declarations without updating the lock file'
PACKAGE_JSON = '{\n  "name": "web",\n  "version": "1.0.0",\n  "dependencies": {\n    "left-pad": "^1.3.0"\n  }\n}\n'
PYPROJECT = '[project]\nname = "calc"\nversion = "1.0.0"\ndependencies = ["requests>=2"]\n'
GO_MOD = 'module example.com/calc\n\ngo 1.22\n\nrequire (\n\tgithub.com/pkg/errors v0.9.1\n)\n'
CSPROJ = '<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n    <PackageReference Include="Serilog" Version="3.1.1" />\n  </ItemGroup>\n</Project>\n'
COMMITTED_FILES = {
    'web/package.json': PACKAGE_JSON,
    'web/package-lock.json': '{"lockfileVersion": 3}\n',
    'py/pyproject.toml': PYPROJECT,
    'py/uv.lock': 'version = 1\n',
    'go/go.mod': GO_MOD,
    'go/go.sum': 'github.com/pkg/errors v0.9.1 h1:x\n',
    'net/Calc.csproj': CSPROJ,
    'net/packages.lock.json': '{"version": 1}\n',
    'nolock/package.json': PACKAGE_JSON,
}


def user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def tool(name, arguments, call_id='c1'):
    return {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': name, 'arguments': arguments}}


def agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': HOOK_TIMESTAMP}}}


@unittest.skipUnless(shutil.which('git'), 'git is required to compare with HEAD.')
class DetectLockfileDriftTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-lockfile-drift-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.workspace = os.path.join(self.root, 'workspace')
        os.makedirs(self.workspace)
        for path, content in COMMITTED_FILES.items():
            self.write_file(path, content)
        self.git('init', '-q')
        self.git('add', '-A')
        self.git('-c', 'user.name=test', '-c', 'user.email=test@example.com', '-c', 'commit.gpgsign=false',
                 'commit', '-q', '-m', 'init')
        self.transcript_path = os.path.join(self.root, 'events.jsonl')
        self.payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': HOOK_TIMESTAMP,
            'cwd': self.workspace,
            'transcriptPath': self.transcript_path,
        }

    def git(self, *arguments):
        subprocess.run(['git', *arguments], cwd=self.workspace, check=True, stdout=subprocess.DEVNULL)

    def write_file(self, path, content):
        full_path = os.path.join(self.workspace, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def invoke(self, events=None, payload=None, raw=None, arguments=()):
        if events is not None:
            with open(self.transcript_path, 'w', encoding='utf-8', newline='\n') as file:
                for event in [*events, agent_stop_start()]:
                    file.write(json.dumps(event, ensure_ascii=False) + '\n')
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def edited(self, path, old, new, tool_name='edit'):
        # ファイルを編集した依頼のトランスクリプト。
        with open(os.path.join(self.workspace, path), encoding='utf-8') as file:
            content = file.read()
        self.write_file(path, content.replace(old, new, 1))
        return [user('依存を追加して'), tool(tool_name, {'path': path, 'old_str': old, 'new_str': new}), assistant('追加した。')]

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_block(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith(REASON_PREFIX))
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_definitions_are_valid_json(self):
        with open(definitions_path, encoding='utf-8') as file:
            self.assertTrue(json.load(file)['manifests'])

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        entries = configuration['hooks']['agentStop']
        relative_path = '.github/hooks/helpers/events/agentStop/detect-lockfile-drift/detect-lockfile-drift.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_dependency_changes_with_stale_lock(self):
        cases = [
            ('npm', 'web/package.json', '"left-pad": "^1.3.0"', '"left-pad": "^1.3.0",\n    "lodash": "^4.17.21"',
             'web/package.json: web/package-lock.json unchanged; run `npm install`'),
            ('uv', 'py/pyproject.toml', '["requests>=2"]', '["requests>=2", "rich>=13"]',
             'py/pyproject.toml: py/uv.lock unchanged; run `uv lock`'),
            ('go', 'go/go.mod', '\tgithub.com/pkg/errors v0.9.1\n', '\tgithub.com/pkg/errors v0.9.2\n',
             'go/go.mod: go/go.sum unchanged; run `go mod tidy`'),
            ('nuget', 'net/Calc.csproj', 'Version="3.1.1"', 'Version="4.0.0"',
             'net/Calc.csproj: net/packages.lock.json unchanged; run `dotnet restore --force-evaluate`'),
        ]
        for name, path, old, new, expected in cases:
            with self.subTest(name=name):
                self.assert_block(self.invoke(self.edited(path, old, new)), expected)
                self.git('checkout', '-q', '--', '.')

    def test_apply_patch_paths_are_used(self):
        self.write_file('web/package.json', PACKAGE_JSON.replace('^1.3.0', '^2.0.0'))
        patch = '*** Begin Patch\n*** Update File: web/package.json\n@@\n-    "left-pad": "^1.3.0"\n+    "left-pad": "^2.0.0"\n*** End Patch\n'
        self.assert_block(
            self.invoke([user('更新して'), tool('apply_patch', patch), assistant('更新した。')]),
            'web/package.json',
        )

    def test_silent_when_lock_is_consistent_or_irrelevant(self):
        cases = [
            ('version only', 'web/package.json', '"version": "1.0.0"', '"version": "1.1.0"', None),
            ('formatting only', 'web/package.json', '"left-pad": "^1.3.0"', '"left-pad":   "^1.3.0"', None),
            ('no lock file', 'nolock/package.json', '"^1.3.0"', '"^2.0.0"', None),
            ('lock updated', 'web/package.json', '"^1.3.0"', '"^2.0.0"', ('web/package-lock.json', '{"lockfileVersion": 3, "x": 1}\n')),
            ('new untracked lock', 'py/pyproject.toml', '["requests>=2"]', '["requests>=3"]', ('py/poetry.lock', 'x\n')),
            ('non-dependency toml key', 'py/pyproject.toml', 'version = "1.0.0"', 'version = "2.0.0"', None),
        ]
        for name, path, old, new, extra in cases:
            with self.subTest(name=name):
                events = self.edited(path, old, new)
                if extra:
                    self.write_file(*extra)
                self.assert_silent(self.invoke(events))
                self.git('checkout', '-q', '--', '.')
                self.git('clean', '-q', '-fd')

    def test_reverted_edit_is_silent(self):
        events = self.edited('web/package.json', '"^1.3.0"', '"^2.0.0"')
        self.git('checkout', '-q', '--', '.')
        self.assert_silent(self.invoke(events))

    def test_new_manifest_is_silent(self):
        self.write_file('new/package.json', PACKAGE_JSON)
        self.write_file('new/package-lock.json', '{}\n')
        self.git('add', 'new/package-lock.json')
        self.assert_silent(self.invoke([
            user('作って'), tool('create', {'path': 'new/package.json', 'file_text': PACKAGE_JSON}), assistant('作った。'),
        ]))

    def test_unparseable_manifest_is_silent(self):
        self.assert_silent(self.invoke(self.edited('web/package.json', '"left-pad": "^1.3.0"', '"left-pad": ')))

    def test_blocks_only_once_per_request(self):
        events = self.edited('web/package.json', '"^1.3.0"', '"^2.0.0"')
        reason = self.assert_block(self.invoke(events))
        self.assert_silent(self.invoke([*events, user(reason, delivery='queued'), assistant('意図して残した。')]))
        self.assert_block(self.invoke([*events, user('次の依頼'), *events[1:]]))

    def test_skips_when_not_applicable(self):
        events = self.edited('web/package.json', '"^1.3.0"', '"^2.0.0"')
        with self.subTest(name='subagent'):
            self.assert_silent(self.invoke(events, payload={**self.payload, 'agentId': 'sub'}))
        with self.subTest(name='no final response'):
            self.assert_silent(self.invoke(events[:-1]))
        with self.subTest(name='edits before the latest request'):
            self.assert_silent(self.invoke([*events, user('ありがとう'), assistant('どういたしまして。')]))
        with self.subTest(name='outside a repository'):
            outside = os.path.join(self.root, 'outside')
            os.makedirs(outside)
            self.assert_silent(self.invoke(events, payload={**self.payload, 'cwd': outside}))

    def test_invalid_input_exits_with_error(self):
        self.invoke([user('x'), assistant('y')])
        cases = [
            ('empty input', b''),
            ('invalid JSON', b'{'),
            ('non-object', b'[]'),
            ('missing cwd', json.dumps({**self.payload, 'cwd': None}).encode('utf-8')),
            ('relative cwd', json.dumps({**self.payload, 'cwd': 'workspace'}).encode('utf-8')),
            ('missing transcript', json.dumps({**self.payload, 'transcriptPath': os.path.join(self.root, 'none.jsonl')}).encode('utf-8')),
        ]
        for name, raw in cases:
            with self.subTest(name=name):
                result = self.invoke(raw=raw)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to detect lockfile drift', result.stderr, name)
        with self.subTest(name='invalid transcript line'):
            with open(self.transcript_path, 'w', encoding='utf-8') as file:
                file.write('not json\n')
            self.assertEqual(self.invoke().returncode, 2)

    def test_invalid_definitions_exit_with_error(self):
        manifest = {'name': 'x', 'filePattern': 'x', 'format': 'json', 'dependencyKeys': ['a'],
                    'lockFiles': [{'file': 'x.lock', 'command': 'x'}]}
        cases = [
            ('not an object', []),
            ('empty manifests', {'manifests': []}),
            ('unknown format', {'manifests': [{**manifest, 'format': 'xml'}]}),
            ('empty lock files', {'manifests': [{**manifest, 'lockFiles': []}]}),
            ('missing keys', {'manifests': [{**manifest, 'dependencyKeys': []}]}),
            ('invalid regex', {'manifests': [{**manifest, 'filePattern': '('}]}),
            ('lines without patterns', {'manifests': [{**manifest, 'format': 'lines'}]}),
        ]
        self.invoke([user('x'), assistant('y')])
        for name, definitions in cases:
            with self.subTest(name=name):
                path = os.path.join(self.root, 'definitions.json')
                with open(path, 'w', encoding='utf-8') as file:
                    json.dump(definitions, file)
                self.assertEqual(self.invoke(arguments=('--definitions-path', path)).returncode, 2, name)


if __name__ == '__main__':
    run_tests()
