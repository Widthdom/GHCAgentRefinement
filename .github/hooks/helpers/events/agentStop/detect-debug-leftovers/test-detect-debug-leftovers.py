import json
import os
import subprocess
import sys
import tempfile
import unittest


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'detect-debug-leftovers.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


HOOK_TIMESTAMP = 1777777777000
REASON_PREFIX = 'The changes in this request leave debugging code'


def user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def tool(name, arguments, call_id='c1'):
    return {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': name, 'arguments': arguments}}


def agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': HOOK_TIMESTAMP}}}


class DetectDebugLeftoversTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-debug-leftovers-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.workspace = os.path.join(self.root, 'workspace')
        os.makedirs(self.workspace)
        self.transcript_path = os.path.join(self.root, 'events.jsonl')
        self.payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': HOOK_TIMESTAMP,
            'cwd': self.workspace,
            'transcriptPath': self.transcript_path,
        }

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

    def created(self, path, content, response='直した。'):
        # ファイルを作成した依頼のトランスクリプト。
        self.write_file(path, content)
        return [user('直して'), tool('create', {'path': path, 'file_text': content}), assistant(response)]

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
        relative_path = '.github/hooks/helpers/events/agentStop/detect-debug-leftovers/detect-debug-leftovers.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_debug_leftovers(self):
        for path, line, kind in [
            ('a.ts', 'console.log(value);', 'console output'),
            ('a.js', 'console.debug("state", state);', 'console output'),
            ('a.js', 'debugger;', 'debugger statement'),
            ('a.py', 'breakpoint()', 'Python debugger'),
            ('a.py', 'import pdb; pdb.set_trace()', 'Python debugger'),
            ('a.py', 'import ipdb', 'Python debugger'),
            ('a.rb', 'binding.pry', 'Ruby debugger'),
            ('a.rb', 'byebug', 'Ruby debugger'),
            ('a.rs', 'let x = dbg!(y);', 'Rust dbg macro'),
            ('a.php', 'var_dump($user);', 'PHP dump'),
            ('A.cs', 'System.Diagnostics.Debugger.Break();', '.NET debugger'),
            ('a.py', 'print(f"DEBUG: {value}")', 'debug-labeled output'),
            ('a.py', "print('>>> here', x)", 'debug-labeled output'),
            ('a.py', 'print(">>> here", file=sys.stderr)', 'debug-labeled output'),
            ('a.go', 'fmt.Println("XXX", v)', 'debug-labeled output'),
            ('a.go', 'fmt.Fprintln(os.Stderr, "DEBUG", v)', 'debug-labeled output'),
            ('A.java', 'System.out.println("HERE");', 'debug-labeled output'),
            ('A.cs', 'Console.WriteLine("[DEBUG] start");', 'debug-labeled output'),
            ('a.rs', 'eprintln!("debug: {}", x);', 'debug-labeled output'),
            ('a.ps1', 'Write-Host "*** value: $v"', 'debug-labeled output'),
        ]:
            with self.subTest(line=line):
                events = self.created(path, f'x = 1\n    {line}\n')
                self.assert_block(self.invoke(events), f'- {path}: {kind} ("{line}")')

    def test_allows_ordinary_output(self):
        for line in (
            "print(json.dumps(output), file=sys.stdout)",
            "print(f'Failed to load: {ex}', file=sys.stderr)",
            'logger.debug("cache miss")',
            "print('Debug mode is enabled.')",
            'Console.WriteLine("Done");',
            'debugger_enabled = True',
            'const isDebugger = debugger_flag;',
        ):
            with self.subTest(line=line):
                self.assert_silent(self.invoke(self.created('a.py', f'{line}\n')))

    def test_edit_and_patch_arguments(self):
        self.write_file('a.ts', 'function f() {\n    console.log(x);\n    return x;\n}\n')
        self.write_file('b.py', 'def f():\n    breakpoint()\n    return 1\n')
        patch = '*** Begin Patch\n*** Update File: b.py\n def f():\n+    breakpoint()\n     return 1\n*** End Patch'
        events = [
            user('直して'),
            tool('functions.edit', json.dumps({
                'path': 'a.ts', 'old_str': 'return x;', 'new_str': 'console.log(x);\n    return x;',
            })),
            tool('apply_patch', patch, call_id='c2'),
            assistant('Done.'),
        ]
        self.assert_block(self.invoke(events), '- a.ts: console output', '- b.py: Python debugger')

    def test_ignores_existing_removed_and_excluded_lines(self):
        self.write_file('kept.ts', 'console.log(x);\nlet y;\n')
        self.write_file('gone.py', 'x = 1\n')
        for path in ('notes.md', 'config.json'):
            self.write_file(path, 'console.log(x);\n')
        events = [
            user('直して'),
            # 置換前の内容にもある行は、元からあるものとみなす。
            tool('edit', {'path': 'kept.ts', 'old_str': 'console.log(x);', 'new_str': 'console.log(x);\nlet y;'}),
            # 後の編集でファイルから消えたデバッグコードは対象外。
            tool('create', {'path': 'gone.py', 'file_text': 'breakpoint()\nx = 1\n'}, 'c2'),
            tool('create', {'path': 'notes.md', 'file_text': 'console.log(x);\n'}, 'c3'),
            tool('create', {'path': 'config.json', 'file_text': 'console.log(x);\n'}, 'c4'),
            # 閲覧、削除されたファイルへの書き込み、シェルは対象外。
            tool('str_replace_editor', {'command': 'view', 'path': 'kept.ts', 'new_str': 'debugger;'}, 'c5'),
            tool('create', {'path': 'deleted.js', 'file_text': 'debugger;'}, 'c6'),
            tool('bash', {'command': 'echo "debugger;" > x.js'}, 'c7'),
            assistant('直した。'),
        ]
        self.assert_silent(self.invoke(events))

    def test_only_latest_request_is_checked(self):
        events = [*self.created('a.js', 'debugger;\n'), user('READMEを直して'), assistant('直した。')]
        self.assert_silent(self.invoke(events))

    def test_blocks_only_once_per_request(self):
        events = self.created('a.js', 'debugger;\n')
        reason = self.assert_block(self.invoke(events))
        self.assert_silent(self.invoke([*events, user(reason, delivery='queued'), assistant('消した。')]))
        # 割り込みのメッセージは依頼の区切りにならない。
        self.assert_block(self.invoke([*events, user('急いで', delivery='queued'), assistant('直した。')]))

    def test_skips_subagents_and_turns_without_response(self):
        self.assert_silent(self.invoke(self.created('a.js', 'debugger;\n'), payload={**self.payload, 'agentId': 'sub'}))
        self.assert_silent(self.invoke([user('直して'), tool('create', {'path': 'a.js', 'file_text': 'x'})]))

    def test_reports_each_line_once_and_limits_findings(self):
        reason = self.assert_block(self.invoke(self.created('a.js', 'console.log(">>> here");\n')))
        self.assertEqual(reason.count('>>> here'), 1)
        content = ''.join(f'console.log("item {index} ");\n' for index in range(12))
        reason = self.assert_block(self.invoke(self.created('a.js', content)), 'item 9 ')
        self.assertNotIn('item 10 ', reason)

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
                self.assertIn(b'Failed to detect debug leftovers:', result.stderr)
        with open(self.transcript_path, 'w', encoding='utf-8') as file:
            file.write('{broken\n')
        result = self.invoke()
        self.assertEqual((result.returncode, result.stdout), (2, b''))

    def test_invalid_definitions(self):
        valid_patterns = [{'name': 'x', 'pattern': 'x'}]
        for name, content in [
            ('not object', '[]'),
            ('broken json', '{'),
            ('missing excluded', json.dumps({'debugPatterns': valid_patterns})),
            ('empty patterns', json.dumps({'excludedFilePatterns': [], 'debugPatterns': []})),
            ('pattern without name', json.dumps({'excludedFilePatterns': [], 'debugPatterns': [{'pattern': 'x'}]})),
            ('invalid regex', json.dumps({'excludedFilePatterns': ['('], 'debugPatterns': valid_patterns})),
        ]:
            with self.subTest(name=name):
                definitions_path = os.path.join(self.root, 'definitions.json')
                with open(definitions_path, 'w', encoding='utf-8') as file:
                    file.write(content)
                result = self.invoke(self.created('a.js', 'x\n'), arguments=('--definitions-path', definitions_path))
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect debug leftovers:', result.stderr)


if __name__ == '__main__':
    run_tests()
