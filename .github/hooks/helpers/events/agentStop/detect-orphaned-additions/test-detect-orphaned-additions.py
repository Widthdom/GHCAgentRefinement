import json
import os
import subprocess
import sys
import tempfile
import unittest


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'detect-orphaned-additions.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


HOOK_TIMESTAMP = 1777777777000
REASON_PREFIX = 'The changes in this request add definitions that nothing uses'


def user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def tool(name, arguments, call_id='c1'):
    return {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': name, 'arguments': arguments}}


def agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': HOOK_TIMESTAMP}}}


class DetectOrphanedAdditionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-orphaned-additions-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.workspace = os.path.join(self.root, 'workspace')
        os.makedirs(self.workspace)
        subprocess.run(['git', 'init', '-q', self.workspace], check=True)
        self.transcript_path = os.path.join(self.root, 'events.jsonl')
        self.payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': HOOK_TIMESTAMP,
            'cwd': self.workspace,
            'transcriptPath': self.transcript_path,
        }

    def write_file(self, path, content, base=None):
        full_path = os.path.join(base or self.workspace, path)
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

    def created(self, path, content, response='追加した。'):
        # ファイルを作成した依頼のトランスクリプト。
        self.write_file(path, content)
        return [user('追加して'), tool('create', {'path': path, 'file_text': content}), assistant(response)]

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

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        entries = configuration['hooks']['agentStop']
        relative_path = '.github/hooks/helpers/events/agentStop/detect-orphaned-additions/detect-orphaned-additions.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_unreferenced_definitions(self):
        for path, content, name, line_number in [
            ('src/Calc.cs', 'class Calc\n{\n    public static int Twice(int x) => x * 2;\n}\n', 'Twice', 3),
            ('src/Calc.cs', 'namespace A;\n\npublic sealed record Point(int X, int Y);\n', 'Point', 3),
            ('src/Calc.cs', 'class C\n{\n    private async Task<List<int>> LoadAsync(int id)\n    {\n    }\n}\n',
             'LoadAsync', 3),
            ('src/Calc.java', 'class C {\n    public static List<String> names(int x) {\n    }\n}\n', 'names', 2),
            ('src/calc.py', 'import os\n\n\ndef helper(x):\n    return x\n', 'helper', 4),
            ('src/calc.py', 'class Parser:\n    pass\n', 'Parser', 1),
            ('src/calc.ts', 'export function formatDate(d: Date) {\n}\n', 'formatDate', 1),
            ('src/calc.ts', 'export const toSlug = (s: string): string => s;\n', 'toSlug', 1),
            ('src/calc.ts', 'export interface Options {\n}\n', 'Options', 1),
            ('src/calc.go', 'package calc\n\nfunc (c *Calc) Reset() {\n}\n', 'Reset', 3),
        ]:
            with self.subTest(name=name):
                result = self.invoke(self.created(path, content))
                self.assert_block(result, f'- {path}:{line_number}: {name} (no references)')

    def test_allows_referenced_definitions(self):
        # 別ファイル（未追跡を含む）や同じファイルの別の行からの参照があれば対象外。
        self.write_file('src/app.py', 'from calc import helper\n\nprint(helper(1))\n')
        self.assert_silent(self.invoke(self.created('src/calc.py', 'def helper(x):\n    return x\n')))
        content = 'def _inner(x):\n    return x\n\n\ndef run():\n    return _inner(1)\n'
        self.write_file('src/app.py', 'from calc import run\n')
        self.assert_silent(self.invoke(self.created('src/calc.py', content)))

    def test_reports_definitions_referenced_only_from_tests(self):
        self.write_file('tests/test_calc.py', 'from calc import helper\n\nassert helper(1) == 1\n')
        self.write_file('docs/usage.md', 'Call `helper` from your code.\n')
        result = self.invoke(self.created('src/calc.py', 'def helper(x):\n    return x\n'))
        self.assert_block(result, '- src/calc.py:1: helper (referenced only from tests)')

    def test_ignores_references_in_documents(self):
        self.write_file('README.md', 'Use helper(x).\n')
        result = self.invoke(self.created('src/calc.py', 'def helper(x):\n    return x\n'))
        self.assert_block(result, '- src/calc.py:1: helper (no references)')

    def test_skips_entry_points_and_framework_callbacks(self):
        for path, content in [
            # 除外する名前。
            ('src/Program.cs', 'class A\n{\n    public static void Main(string[] args) { }\n}\n'),
            ('src/A.cs', 'sealed class A : IDisposable\n{\n    public void Dispose() { }\n}\n'),
            ('src/calc.py', 'def main():\n    pass\n'),
            ('src/calc.go', 'package calc\n\nfunc init() {\n}\n'),
            # overrideや属性・デコレーター付きの定義は基底クラスやフレームワークから呼ばれる。
            ('src/A.cs', 'class A : B\n{\n    public override string Describe() => "";\n}\n'),
            ('src/A.cs', 'class A : ControllerBase\n{\n    [HttpGet]\n    public IActionResult Get() => Ok();\n}\n'),
            ('src/A.java', 'class A extends B {\n    @Override\n    public String describe() {\n    }\n}\n'),
            ('src/app.py', 'app = Flask(__name__)\n\n\n@app.route("/")\n\ndef index():\n    pass\n'),
            # テストファイル内の定義はテストフレームワークが呼ぶ。
            ('tests/test_calc.py', 'def test_helper():\n    pass\n'),
            ('src/CalcTests.cs', 'class CalcTests\n{\n    public void Adds() { }\n}\n'),
            # 対象言語でないファイル、コンストラクターやフィールドの宣言。
            ('notes.md', 'def helper(x):\n'),
            ('src/A.cs', 'partial class A : B\n{\n    public A(int x) : base(x) { }\n    private readonly int _x = Compute(1);\n}\n'),
        ]:
            with self.subTest(path=path, content=content):
                # 型名そのものは対象にしないよう、別ファイルから参照しておく。
                self.write_file('src/Usage.cs', 'var a = new A(1);\n')
                self.assert_silent(self.invoke(self.created(path, content)))

    def test_edit_and_patch_arguments(self):
        self.write_file('src/calc.py', 'def existing():\n    pass\n\n\ndef added(x):\n    return x\n')
        self.write_file('src/calc.ts', 'export function fresh() {\n}\n')
        self.write_file('src/main.py', 'from calc import existing\n')
        patch = '*** Begin Patch\n*** Add File: src/calc.ts\n+export function fresh() {\n+}\n*** End Patch'
        events = [
            user('追加して'),
            tool('functions.edit', json.dumps({
                'path': 'src/calc.py', 'old_str': '    pass\n', 'new_str': '    pass\n\n\ndef added(x):\n    return x\n',
            })),
            tool('apply_patch', patch, call_id='c2'),
            assistant('Done.'),
        ]
        self.assert_block(
            self.invoke(events), '- src/calc.py:5: added (no references)', '- src/calc.ts:1: fresh (no references)',
        )

    def test_ignores_existing_removed_and_unrelated_writes(self):
        self.write_file('src/kept.py', 'def kept():\n    pass\n')
        self.write_file('src/gone.py', 'x = 1\n')
        events = [
            user('直して'),
            # 置換前の内容にもある定義は、元からあるものとみなす。
            tool('edit', {'path': 'src/kept.py', 'old_str': 'def kept():\n    return 1', 'new_str': 'def kept():\n    pass'}),
            # 後の編集でファイルから消えた定義は対象外。
            tool('create', {'path': 'src/gone.py', 'file_text': 'def gone():\n    pass\n'}, 'c2'),
            # 閲覧、削除されたファイルへの書き込み、シェルは対象外。
            tool('str_replace_editor', {'command': 'view', 'path': 'src/kept.py', 'new_str': 'def viewed():'}, 'c3'),
            tool('create', {'path': 'src/deleted.py', 'file_text': 'def deleted():\n    pass\n'}, 'c4'),
            tool('bash', {'command': 'echo "def shell():" > src/shell.py'}, 'c5'),
            assistant('直した。'),
        ]
        self.assert_silent(self.invoke(events))

    def test_skips_files_outside_repository_and_without_git(self):
        outside = os.path.join(self.root, 'outside')
        self.write_file('calc.py', 'def helper(x):\n    return x\n', base=outside)
        events = [user('追加して'), tool('create', {'path': os.path.join(outside, 'calc.py')}), assistant('追加した。')]
        self.assert_silent(self.invoke(events))
        self.assert_silent(self.invoke(events, payload={**self.payload, 'cwd': outside}))

    def test_only_latest_request_is_checked(self):
        events = [*self.created('src/calc.py', 'def helper(x):\n    return x\n'), user('READMEを直して'), assistant('直した。')]
        self.assert_silent(self.invoke(events))

    def test_blocks_only_once_per_request(self):
        events = self.created('src/calc.py', 'def helper(x):\n    return x\n')
        reason = self.assert_block(self.invoke(events))
        self.assert_silent(self.invoke([*events, user(reason, delivery='queued'), assistant('公開APIとして残す。')]))
        # 割り込みのメッセージは依頼の区切りにならない。
        self.assert_block(self.invoke([*events, user('急いで', delivery='queued'), assistant('追加した。')]))

    def test_skips_subagents_and_turns_without_response(self):
        events = self.created('src/calc.py', 'def helper(x):\n    return x\n')
        self.assert_silent(self.invoke(events, payload={**self.payload, 'agentId': 'sub'}))
        self.assert_silent(self.invoke(events[:2]))

    def test_reports_each_definition_once_and_limits_findings(self):
        reason = self.assert_block(self.invoke(self.created('src/calc.py', 'def helper(x):\n    return x\n')))
        self.assertEqual(reason.count('helper'), 1)
        content = ''.join(f'def item_{index:02}():\n    pass\n' for index in range(12))
        reason = self.assert_block(self.invoke(self.created('src/calc.py', content)), 'item_09')
        self.assertNotIn('item_10', reason)

    def test_invalid_input(self):
        for name, raw in [
            ('empty', b''),
            ('broken json', b'{'),
            ('invalid utf-8', b'\xff'),
            ('not object', b'[]'),
            ('missing cwd', json.dumps({'transcriptPath': self.transcript_path}).encode('utf-8')),
            ('relative cwd', json.dumps({'cwd': 'x', 'transcriptPath': self.transcript_path}).encode('utf-8')),
            ('missing transcript', json.dumps({'cwd': self.workspace}).encode('utf-8')),
            ('transcript not file', json.dumps({'cwd': self.workspace, 'transcriptPath': self.workspace}).encode('utf-8')),
        ]:
            with self.subTest(name=name):
                result = self.invoke(raw=raw)
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect orphaned additions:', result.stderr)
        with open(self.transcript_path, 'w', encoding='utf-8') as file:
            file.write('{broken\n')
        result = self.invoke()
        self.assertEqual((result.returncode, result.stdout), (2, b''))

    def test_invalid_definitions(self):
        language = {'name': 'Python', 'filePattern': r'\.py$', 'definitionPatterns': [r'^def (?P<name>\w+)']}
        valid = {'ignoredReferenceFilePatterns': [], 'excludedNamePatterns': [], 'languages': [language]}
        for name, content in [
            ('not object', '[]'),
            ('broken json', '{'),
            ('missing excluded names', json.dumps({**valid, 'excludedNamePatterns': None})),
            ('empty languages', json.dumps({**valid, 'languages': []})),
            ('language without name', json.dumps({**valid, 'languages': [{**language, 'name': ''}]})),
            ('empty definition patterns', json.dumps({**valid, 'languages': [{**language, 'definitionPatterns': []}]})),
            ('pattern without name group', json.dumps({**valid, 'languages': [{**language, 'definitionPatterns': ['^def']}]})),
            ('empty annotation', json.dumps({**valid, 'languages': [{**language, 'annotationPattern': ''}]})),
            ('invalid regex', json.dumps({**valid, 'excludedNamePatterns': ['(']})),
        ]:
            with self.subTest(name=name):
                definitions_path = os.path.join(self.root, 'definitions.json')
                with open(definitions_path, 'w', encoding='utf-8') as file:
                    file.write(content)
                result = self.invoke(
                    self.created('src/calc.py', 'def helper():\n    pass\n'),
                    arguments=('--definitions-path', definitions_path),
                )
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect orphaned additions:', result.stderr)
        test_definitions_path = os.path.join(self.root, 'test-definitions.json')
        with open(test_definitions_path, 'w', encoding='utf-8') as file:
            file.write(json.dumps({'testFilePatterns': []}))
        result = self.invoke(
            self.created('src/calc.py', 'def helper():\n    pass\n'),
            arguments=('--test-definitions-path', test_definitions_path),
        )
        self.assertEqual((result.returncode, result.stdout), (2, b''))
        self.assertIn(b'Failed to detect orphaned additions:', result.stderr)


if __name__ == '__main__':
    run_tests()
