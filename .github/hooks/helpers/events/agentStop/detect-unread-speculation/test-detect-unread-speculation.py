import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'detect-unread-speculation.py')
definitions_path = os.path.join(script_directory, 'unread-speculation-definitions.json')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


HOOK_TIMESTAMP = 1777777777000


def user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def tool(call_id, tool_name, arguments):
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': tool_name, 'arguments': arguments}},
        {'type': 'tool.execution_complete', 'data': {'toolCallId': call_id, 'success': True}},
    ]


def agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': HOOK_TIMESTAMP}}}


class DetectUnreadSpeculationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-unread-speculation-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.session_id = str(uuid.uuid4())
        self.state_path = os.path.join(
            self.root, 'ghc-agent-refinement', 'hook-states', 'known-files', f'{self.session_id}.sqlite3',
        )
        self.workspace = os.path.join(self.root, 'workspace')
        os.makedirs(os.path.join(self.workspace, 'src'))
        for name in ('a.py', 'b.py', '対象.py'):
            self.write_file(name)
        self.transcript_path = os.path.join(self.root, 'events.jsonl')
        self.payload = {
            'sessionId': self.session_id,
            'timestamp': HOOK_TIMESTAMP,
            'cwd': self.workspace,
            'transcriptPath': self.transcript_path,
        }

    def write_file(self, name):
        with open(os.path.join(self.workspace, 'src', name), 'w', encoding='utf-8') as file:
            file.write('a\n')

    def write_transcript(self, events):
        with open(self.transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            for event in events:
                file.write(json.dumps(event, ensure_ascii=False) + '\n')

    def invoke(self, events=None, payload=None, raw=None, definitions_path=None):
        if events is not None:
            self.write_transcript([*events, agent_stop_start()])
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        options = [] if definitions_path is None else ['--definitions-path', definitions_path]
        return subprocess.run(
            [sys.executable, hook_path, *options], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=15,
        )

    def record(self, relative_path, state='seen'):
        key = hashlib.sha256(
            os.path.normcase(os.path.realpath(os.path.join(self.workspace, relative_path))).encode('utf-8')
        ).hexdigest()
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with closing(sqlite3.connect(self.state_path)) as connection, connection:
            connection.execute('CREATE TABLE IF NOT EXISTS files (file TEXT PRIMARY KEY, state TEXT NOT NULL)')
            connection.execute('INSERT OR REPLACE INTO files (file, state) VALUES (?, ?)', (key, state))

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_block(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith('The final response speculates about files'))
        self.assertIn('state explicitly that the file was not checked.', output['reason'])
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        self.assertEqual(configuration['version'], 1)
        entries = configuration['hooks']['agentStop']
        relative_path = '.github/hooks/helpers/events/agentStop/detect-unread-speculation/detect-unread-speculation.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_definitions_json_is_valid(self):
        with open(definitions_path, 'rb') as file:
            content = file.read()
        self.assertFalse(content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(set(json.loads(content.decode('utf-8'))), {'speculationPatterns'})

    def test_blocks_speculation_about_unread_file(self):
        cases = (
            ('`src/a.py` はおそらく入力を検証していない。', 'src/a.py'),
            ('[a.py](src/a.py) で例外を握りつぶしているはずだ。', 'src/a.py'),
            ('src/a.py:12 の処理は重複しているかもしれない。', 'src/a.py'),
            ('`src/a.py#L3-L5` で設定を読んでいると思われる。', 'src/a.py'),
            ('`src/対象.py` は古い形式のようだ。', 'src/対象.py'),
            ('The parser in `src/a.py` probably ignores comments.', 'src/a.py'),
            ('I think src/b.py handles retries.', 'src/b.py'),
        )
        for response, fragment in cases:
            with self.subTest(response=response):
                self.assert_block(self.invoke([user('調べて'), assistant(response)]), fragment)

    def test_allows_files_recorded_as_seen(self):
        self.record('src/a.py')
        self.assert_silent(self.invoke([user('調べて'), assistant('`src/a.py` はおそらく入力を検証していない。')]))

    def test_denied_files_are_still_unread(self):
        self.record('src/a.py', 'denied')
        self.assert_block(self.invoke([user('調べて'), assistant('`src/a.py` はおそらく古い。')]), 'src/a.py')

    def test_allows_files_read_by_commands(self):
        absolute = os.path.join(self.workspace, 'src', 'a.py')
        reads = (
            tool('c1', 'bash', {'command': 'cat src/a.py'}),
            tool('c1', 'functions.powershell', {'command': f'Get-Content "{absolute}"'}),
            tool('c1', 'rg', {'pattern': 'def', 'path': 'src/a.py'}),
            tool('c1', 'grep', {'pattern': 'def', 'paths': [absolute]}),
        )
        for read in reads:
            with self.subTest(read=read):
                events = [user('前の依頼'), *read, user('調べて'), assistant('`src/a.py` はおそらく古い。')]
                self.assert_silent(self.invoke(events))

    def test_other_tool_arguments_do_not_count_as_reads(self):
        events = [
            user('調べて'), *tool('c1', 'edit', {'path': 'src/a.py'}), *tool('c2', 'bash', {'command': 'cat src/b.py'}),
            assistant('`src/a.py` と `src/b.py` はおそらく古い。'),
        ]
        reason = self.assert_block(self.invoke(events), 'src/a.py')
        self.assertNotIn('src/b.py', reason)

    def test_ignores_non_speculative_or_unrelated_statements(self):
        responses = (
            '`src/a.py` は入力を検証していない。',
            'おそらく問題ない。\n`src/a.py` を確認した。',
            '`src/a.py` の `probably_ok` 関数を呼ぶ。',
            '`src/missing.py` はおそらく古い。',
            'おそらく `origin/main` の変更が原因だ。',
            '結果:\n```\nsrc/a.py はおそらく古い\n```\n',
            'おそらく https://example.com/src/a.py にある。',
            '',
        )
        for response in responses:
            with self.subTest(response=response):
                self.assert_silent(self.invoke([user('調べて'), assistant(response)]))

    def test_resolves_paths_from_repository_root(self):
        subprocess.run(['git', 'init', '-q', self.workspace], check=True, timeout=10)
        payload = {**self.payload, 'cwd': os.path.join(self.workspace, 'src')}
        self.write_transcript([user('調べて'), assistant('`src/a.py` はおそらく古い。'), agent_stop_start()])
        self.assert_block(self.invoke(payload=payload), 'src/a.py')

    def test_reported_files_are_limited(self):
        names = [f'f{index:02}.py' for index in range(12)]
        for name in names:
            self.write_file(name)
        response = 'おそらく ' + ' '.join(f'`src/{name}`' for name in names) + ' は古い。'
        reason = self.assert_block(self.invoke([user('調べて'), assistant(response)]), 'src/f09.py and 2 more')
        self.assertNotIn('src/f10.py', reason)

    def test_blocks_only_once_per_request(self):
        first = [user('調べて'), assistant('`src/a.py` はおそらく古い。')]
        reason = self.assert_block(self.invoke(first))
        self.assert_silent(self.invoke([*first, user(reason, 'queued'), assistant('`src/a.py` はおそらく古い。')]))
        self.assert_block(self.invoke([*first, user(reason, 'queued'), user('次の依頼'), assistant('`src/a.py` はたぶん古い。')]))

    def test_queued_messages_do_not_reset_the_request(self):
        events = [
            user('調べて'), *tool('c1', 'bash', {'command': 'cat src/a.py'}),
            user('The final response footer is missing or incorrect.', 'queued'),
            assistant('`src/a.py` はおそらく古い。'),
        ]
        self.assert_silent(self.invoke(events))

    def test_last_assistant_message_is_checked(self):
        events = [user('調べて'), assistant('`src/a.py` はおそらく古い。'), assistant('`src/a.py` は未確認。')]
        self.assert_silent(self.invoke(events))

    def test_skips_subagents(self):
        self.write_transcript([user('調べて'), assistant('`src/a.py` はおそらく古い。')])
        self.assert_silent(self.invoke(payload={**self.payload, 'agentId': 'child-agent'}))

    def test_waits_for_agent_stop_event(self):
        self.write_transcript([user('調べて')])
        process = subprocess.Popen(
            [sys.executable, hook_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.environment,
        )
        process.stdin.write(json.dumps(self.payload).encode('utf-8'))
        process.stdin.close()
        process.stdin = None
        time.sleep(0.5)
        with open(self.transcript_path, 'a', encoding='utf-8', newline='\n') as file:
            for event in (assistant('`src/a.py` はおそらく古い。'), agent_stop_start()):
                file.write(json.dumps(event, ensure_ascii=False) + '\n')
        output, error = process.communicate(timeout=15)
        self.assert_block(subprocess.CompletedProcess([], process.returncode, output, error))

    def test_custom_definitions_are_used(self):
        custom_path = os.path.join(self.root, 'custom.json')
        with open(custom_path, 'w', encoding='utf-8-sig') as file:
            json.dump({'speculationPatterns': ['きっと']}, file, ensure_ascii=False)
        self.assert_block(self.invoke([user('調べて'), assistant('`src/a.py` はきっと古い。')], definitions_path=custom_path))
        self.assert_silent(self.invoke([user('調べて'), assistant('`src/a.py` はおそらく古い。')], definitions_path=custom_path))

    def test_invalid_definitions_report_error(self):
        cases = [('{', 'not valid JSON'), ('[]', 'must be a JSON object'), ('\xff', None)]
        for value in (None, [], [''], [' '], [1], 'a'):
            cases.append((json.dumps({'speculationPatterns': value}), 'non-empty array of non-empty strings'))
        cases.append((json.dumps({'speculationPatterns': ['(']}), 'Invalid regular expression'))
        self.write_transcript([user('調べて'), assistant('`src/a.py` はおそらく古い。'), agent_stop_start()])
        for index, (content, fragment) in enumerate(cases):
            with self.subTest(content=content):
                path = os.path.join(self.root, f'definitions-{index}.json')
                with open(path, 'wb') as file:
                    file.write(content.encode('latin-1') if fragment is None else content.encode('utf-8'))
                result = self.invoke(definitions_path=path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to load unread speculation definitions:', result.stderr)
                if fragment is not None:
                    self.assertIn(fragment.encode('utf-8'), result.stderr)
        for path in (os.path.join(self.root, 'missing.json'), self.root):
            with self.subTest(path=path):
                result = self.invoke(definitions_path=path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Definitions path is not a file', result.stderr)

    def test_invalid_input_reports_error(self):
        self.write_transcript([user('調べて')])
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'\xff']
        for value in (None, '', ' ', 3, [], os.path.join(self.root, 'missing.jsonl'), self.root):
            invalid.append(json.dumps({**self.payload, 'transcriptPath': value}).encode('utf-8'))
        for value in (None, '', 3, 'relative/path'):
            invalid.append(json.dumps({**self.payload, 'cwd': value}).encode('utf-8'))
        for value in (None, '', 'not-a-uuid'):
            invalid.append(json.dumps({**self.payload, 'sessionId': value}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertTrue(result.stderr)

    def test_invalid_transcript_reports_error(self):
        for content in ('{', '[]\n', '{"type":"user.message","data":[]}\n'):
            with self.subTest(content=content):
                with open(self.transcript_path, 'w', encoding='utf-8') as file:
                    file.write(content)
                result = self.invoke(payload={**self.payload, 'timestamp': None})
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to inspect unread speculation:', result.stderr)


if __name__ == '__main__':
    run_tests()
