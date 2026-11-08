import json
import os
import subprocess
import sys
import tempfile
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-temporary-code.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


_HOOK_TIMESTAMP = 1777777777000
_DEBUG_PREFIX = 'The changes in this request leave debugging code'
_PLACEHOLDER_PREFIX = 'The changes in this request leave placeholder code'
_VALID_PATTERNS = [{'name': 'x', 'pattern': 'x'}]
_VALID_DEFINITIONS = {
    'excludedFilePatterns': [],
    'debugPatterns': _VALID_PATTERNS,
    'placeholderPatterns': _VALID_PATTERNS,
    'placeholderDisclosurePatterns': ['x'],
}


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _tool(name, arguments, call_id='c1'):
    return {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': name, 'arguments': arguments}}


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


class DetectTemporaryCodeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-temporary-code-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__workspace = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__workspace)
        self.__transcript_path = os.path.join(self.__root, 'events.jsonl')
        self.__payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': _HOOK_TIMESTAMP,
            'cwd': self.__workspace,
            'transcriptPath': self.__transcript_path,
        }

    def __write_file(self, path, content):
        full_path = os.path.join(self.__workspace, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __invoke(self, events=None, payload=None, raw=None, arguments=()):
        if events is not None:
            with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
                for event in [*events, _agent_stop_start()]:
                    file.write(json.dumps(event, ensure_ascii=False) + '\n')
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def __created(self, path, content, response='直した。'):
        # ファイルを作成した依頼のトランスクリプト。
        self.__write_file(path, content)
        return [_user('直して'), _tool('create', {'path': path, 'file_text': content}), _assistant(response)]

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_block(self, result, prefix, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith(prefix), output['reason'])
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    # デバッグ用のコード

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
                events = self.__created(path, f'x = 1\n    {line}\n')
                reason = self.__assert_block(self.__invoke(events), _DEBUG_PREFIX, f'- {path}: {kind} ("{line}")')
                self.assertIn('Remove them now.', reason)

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
                self.__assert_silent(self.__invoke(self.__created('a.py', f'{line}\n')))

    def test_debug_leftovers_are_not_excused_by_placeholder_disclosure(self):
        events = self.__created('a.js', 'debugger;\n', 'TODOを1件残した。')
        self.__assert_block(self.__invoke(events), _DEBUG_PREFIX, '- a.js: debugger statement')

    # 仮実装

    def test_blocks_placeholders(self):
        for path, line, kind in [
            ('src/Foo.cs', 'throw new NotImplementedException();', 'NotImplementedException'),
            ('src/Foo.cs', 'throw new System.NotImplementedException("later");', 'NotImplementedException'),
            ('foo.py', 'raise NotImplementedError', 'NotImplementedError'),
            ('foo.ts', "throw new Error('Not implemented');", 'not-implemented error'),
            ('Foo.java', 'throw new UnsupportedOperationException("TODO");', 'not-implemented error'),
            ('foo.go', 'panic("not implemented")', 'not-implemented panic'),
            ('foo.rs', 'todo!()', 'todo macro'),
            ('foo.rs', 'unimplemented!("x")', 'todo macro'),
            ('Foo.kt', 'fun f(): Int = TODO()', 'todo macro'),
            ('foo.py', '# TODO: handle errors', 'TODO comment'),
            ('Foo.cs', '// TODO validate input', 'TODO comment'),
            ('foo.sql', '-- TBD', 'TODO comment'),
            ('Foo.cs', '// 後で実装する', 'deferred implementation comment'),
            ('foo.js', '/* implement later */', 'deferred implementation comment'),
        ]:
            with self.subTest(line=line):
                events = self.__created(path, f'line one\n    {line}\n')
                reason = self.__assert_block(self.__invoke(events), _PLACEHOLDER_PREFIX, f'- {path}: {kind} ("{line}")')
                self.assertIn('Implement these parts now.', reason)

    def test_ignores_fixme_comments(self):
        self.__assert_silent(self.__invoke(self.__created('a.py', '# FIXME: overflow when n is negative\n')))

    def test_allows_disclosed_placeholders(self):
        for response in (
            'Parseは未実装のまま残した。',
            '`Save` はスタブにしてある。',
            'TODOを1件残した。',
            'The export path is not implemented yet.',
            'I left a stub for the parser.',
        ):
            with self.subTest(response=response):
                self.__assert_silent(self.__invoke(self.__created('a.cs', 'throw new NotImplementedException();\n', response)))

    # 両方の種類

    def test_edit_and_patch_arguments(self):
        self.__write_file('a.ts', 'function f() {\n    console.log(x);\n    return x;\n}\n')
        self.__write_file('b.py', 'def f():\n    breakpoint()\n    raise NotImplementedError\n')
        patch = (
            '*** Begin Patch\n*** Update File: b.py\n def f():\n+    breakpoint()\n'
            '-    return 1\n+    raise NotImplementedError\n*** End Patch'
        )
        events = [
            _user('直して'),
            _tool('functions.edit', json.dumps({
                'path': 'a.ts', 'old_str': 'return x;', 'new_str': 'console.log(x);\n    return x;',
            })),
            _tool('apply_patch', patch, call_id='c2'),
            _assistant('Done.'),
        ]
        reason = self.__assert_block(self.__invoke(events), _DEBUG_PREFIX, '- a.ts: console output', '- b.py: Python debugger')
        self.assertNotIn('NotImplementedError', reason)
        self.__assert_block(
            self.__invoke([*events, _user(reason, delivery='queued'), _assistant('Done.')]),
            _PLACEHOLDER_PREFIX, '- b.py: NotImplementedError',
        )

    def test_ignores_existing_removed_and_excluded_lines(self):
        self.__write_file('kept.ts', 'console.log(x);\nthrow new NotImplementedException();\nlet y;\n')
        self.__write_file('gone.py', 'x = 1\n')
        for path in ('notes.md', 'config.json'):
            self.__write_file(path, 'console.log(x);\n# TODO: later\n')
        events = [
            _user('直して'),
            # 置換前の内容にもある行は、元からあるものとみなす。
            _tool('edit', {
                'path': 'kept.ts', 'old_str': 'console.log(x);\nthrow new NotImplementedException();',
                'new_str': 'console.log(x);\nthrow new NotImplementedException();\nlet y;',
            }),
            # 後の編集でファイルから消えたものは対象外。
            _tool('create', {'path': 'gone.py', 'file_text': 'breakpoint()\nraise NotImplementedError\nx = 1\n'}, 'c2'),
            _tool('create', {'path': 'notes.md', 'file_text': 'console.log(x);\n# TODO: later\n'}, 'c3'),
            _tool('create', {'path': 'config.json', 'file_text': 'console.log(x);\n# TODO: later\n'}, 'c4'),
            # 閲覧、削除されたファイルへの書き込み、シェルは対象外。
            _tool('str_replace_editor', {'command': 'view', 'path': 'kept.ts', 'new_str': 'debugger;\n# TODO'}, 'c5'),
            _tool('create', {'path': 'deleted.js', 'file_text': 'debugger;\n// TODO'}, 'c6'),
            _tool('bash', {'command': 'echo "debugger; // TODO" > x.js'}, 'c7'),
            _assistant('直した。'),
        ]
        self.__assert_silent(self.__invoke(events))

    def test_only_latest_request_is_checked(self):
        events = [
            *self.__created('a.js', 'debugger;\n// TODO\n'),
            _user('READMEを直して'),
            _assistant('直した。'),
        ]
        self.__assert_silent(self.__invoke(events))

    def test_blocks_each_kind_only_once_per_request(self):
        for path, content, prefix in (
            ('a.js', 'debugger;\n', _DEBUG_PREFIX),
            ('a.cs', 'throw new NotImplementedException();\n', _PLACEHOLDER_PREFIX),
        ):
            with self.subTest(prefix=prefix):
                events = self.__created(path, content)
                reason = self.__assert_block(self.__invoke(events), prefix)
                self.__assert_silent(self.__invoke([*events, _user(reason, delivery='queued'), _assistant('直した。')]))
                # 割り込みのメッセージは依頼の区切りにならない。
                self.__assert_block(self.__invoke([*events, _user('急いで', delivery='queued'), _assistant('直した。')]), prefix)

    def test_reports_one_kind_at_a_time(self):
        events = self.__created('a.js', 'debugger;\n// TODO later\n')
        debug_reason = self.__assert_block(self.__invoke(events), _DEBUG_PREFIX, '- a.js: debugger statement')
        self.assertNotIn('TODO', debug_reason)
        second = [*events, _user(debug_reason, delivery='queued'), _assistant('直した。')]
        placeholder_reason = self.__assert_block(self.__invoke(second), _PLACEHOLDER_PREFIX, '- a.js: TODO comment')
        self.__assert_silent(self.__invoke([*second, _user(placeholder_reason, delivery='queued'), _assistant('直した。')]))

    def test_skips_subagents_and_turns_without_response(self):
        self.__assert_silent(self.__invoke(self.__created('a.js', 'debugger;\n'), payload={**self.__payload, 'agentId': 'sub'}))
        self.__assert_silent(self.__invoke([_user('直して'), _tool('create', {'path': 'a.js', 'file_text': 'x'})]))

    def test_reports_each_line_once_and_limits_findings(self):
        reason = self.__assert_block(self.__invoke(self.__created('a.js', 'console.log(">>> here");\n')), _DEBUG_PREFIX)
        self.assertEqual(reason.count('>>> here'), 1)
        # 同じ行でも、別のファイルに残っていればそれぞれ報告する。
        events = [*self.__created('a.js', 'debugger;\n')[:2], *self.__created('b.js', 'debugger;\n')[1:]]
        self.__assert_block(self.__invoke(events), _DEBUG_PREFIX, '- a.js: debugger statement', '- b.js: debugger statement')
        for content, prefix in (
            (''.join(f'console.log("item {index}.");\n' for index in range(12)), _DEBUG_PREFIX),
            (''.join(f'# TODO item {index}.\n' for index in range(12)), _PLACEHOLDER_PREFIX),
        ):
            with self.subTest(prefix=prefix):
                reason = self.__assert_block(self.__invoke(self.__created('a.js', content)), prefix, 'item 9.')
                self.assertNotIn('item 10.', reason)

    def test_invalid_input(self):
        for name, raw in [
            ('empty', b''),
            ('broken json', b'{'),
            ('invalid utf-8', b'\xff'),
            ('not object', b'[]'),
            ('missing cwd', json.dumps({'transcriptPath': self.__transcript_path}).encode('utf-8')),
            ('relative cwd', json.dumps({'cwd': 'x', 'transcriptPath': self.__transcript_path}).encode('utf-8')),
            ('missing transcript', json.dumps({'cwd': self.__workspace}).encode('utf-8')),
            ('transcript not file', json.dumps({'cwd': self.__workspace, 'transcriptPath': self.__workspace}).encode('utf-8')),
        ]:
            with self.subTest(name=name):
                result = self.__invoke(raw=raw)
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect temporary code:', result.stderr)
        with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write('{broken\n')
        result = self.__invoke()
        self.assertEqual((result.returncode, result.stdout), (2, b''))

    def test_custom_definitions(self):
        path = os.path.join(self.__root, 'definitions.json')
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(json.dumps({**_VALID_DEFINITIONS, 'debugPatterns': [{'name': 'mark', 'pattern': '^MARK$'}]}))
        result = self.__invoke(self.__created('a.js', 'y\nMARK\n'), arguments=('--definitions-path', path))
        self.__assert_block(result, _DEBUG_PREFIX, '- a.js: mark ("MARK")')

    def test_invalid_definitions(self):
        for name, content in [
            ('not object', '[]'),
            ('broken json', '{'),
            ('missing excluded', {key: value for key, value in _VALID_DEFINITIONS.items() if key != 'excludedFilePatterns'}),
            ('blank excluded', {**_VALID_DEFINITIONS, 'excludedFilePatterns': [' ']}),
            ('empty debug patterns', {**_VALID_DEFINITIONS, 'debugPatterns': []}),
            ('debug pattern without name', {**_VALID_DEFINITIONS, 'debugPatterns': [{'pattern': 'x'}]}),
            ('empty placeholder patterns', {**_VALID_DEFINITIONS, 'placeholderPatterns': []}),
            ('placeholder without pattern', {**_VALID_DEFINITIONS, 'placeholderPatterns': [{'name': 'x'}]}),
            ('empty disclosure patterns', {**_VALID_DEFINITIONS, 'placeholderDisclosurePatterns': []}),
            ('invalid excluded regex', {**_VALID_DEFINITIONS, 'excludedFilePatterns': ['(']}),
            ('invalid debug regex', {**_VALID_DEFINITIONS, 'debugPatterns': [{'name': 'x', 'pattern': '('}]}),
            ('invalid disclosure regex', {**_VALID_DEFINITIONS, 'placeholderDisclosurePatterns': ['(']}),
        ]:
            with self.subTest(name=name):
                definitions_path = os.path.join(self.__root, 'definitions.json')
                with open(definitions_path, 'w', encoding='utf-8', newline='\n') as file:
                    file.write(content if isinstance(content, str) else json.dumps(content))
                result = self.__invoke(self.__created('a.js', 'x\n'), arguments=('--definitions-path', definitions_path))
                self.assertEqual((result.returncode, result.stdout), (2, b''), name)
                self.assertIn(b'Failed to detect temporary code:', result.stderr)


if __name__ == '__main__':
    run_tests()
