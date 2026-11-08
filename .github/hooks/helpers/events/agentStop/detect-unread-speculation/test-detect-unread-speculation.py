import hashlib
import json
import os
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-unread-speculation.py')
_definitions_path = os.path.join(_script_directory, 'unread-speculation-definitions.json')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


_HOOK_TIMESTAMP = 1777777777000


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _tool(call_id, tool_name, arguments):
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': tool_name, 'arguments': arguments}},
        {'type': 'tool.execution_complete', 'data': {'toolCallId': call_id, 'success': True}},
    ]


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


class DetectUnreadSpeculationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-unread-speculation-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__root, 'ghc-agent-refinement', 'hook-states', 'known-files', f'{self.__session_id}.sqlite3',
        )
        self.__workspace = os.path.join(self.__root, 'workspace')
        os.makedirs(os.path.join(self.__workspace, 'src'))
        for name in ('a.py', 'b.py', '対象.py'):
            self.__write_file(name)
        self.__transcript_path = os.path.join(self.__root, 'events.jsonl')
        self.__payload = {
            'sessionId': self.__session_id,
            'timestamp': _HOOK_TIMESTAMP,
            'cwd': self.__workspace,
            'transcriptPath': self.__transcript_path,
        }

    def __write_file(self, name):
        with open(os.path.join(self.__workspace, 'src', name), 'w', encoding='utf-8', newline='\n') as file:
            file.write('a\n')

    def __write_transcript(self, events):
        with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            for event in events:
                file.write(json.dumps(event, ensure_ascii=False) + '\n')

    def __invoke(self, events=None, payload=None, raw=None, definitions_path=None):
        if events is not None:
            self.__write_transcript([*events, _agent_stop_start()])
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        options = [] if definitions_path is None else ['--definitions-path', definitions_path]
        return subprocess.run(
            [sys.executable, _hook_path, *options], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=15,
        )

    def __record(self, relative_path, state='seen'):
        key = hashlib.sha256(
            os.path.normcase(os.path.realpath(os.path.join(self.__workspace, relative_path))).encode('utf-8')
        ).hexdigest()
        os.makedirs(os.path.dirname(self.__state_path), exist_ok=True)
        with closing(sqlite3.connect(self.__state_path)) as connection, connection:
            connection.execute('CREATE TABLE IF NOT EXISTS files (file TEXT PRIMARY KEY, state TEXT NOT NULL)')
            connection.execute('INSERT OR REPLACE INTO files (file, state) VALUES (?, ?)', (key, state))

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_block(self, result, *fragments):
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

    def test_definitions_json_is_valid(self):
        with open(_definitions_path, 'rb') as file:
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
                self.__assert_block(self.__invoke([_user('調べて'), _assistant(response)]), fragment)

    def test_allows_files_recorded_as_seen(self):
        self.__record('src/a.py')
        self.__assert_silent(self.__invoke([_user('調べて'), _assistant('`src/a.py` はおそらく入力を検証していない。')]))

    def test_denied_files_are_still_unread(self):
        self.__record('src/a.py', 'denied')
        self.__assert_block(self.__invoke([_user('調べて'), _assistant('`src/a.py` はおそらく古い。')]), 'src/a.py')

    def test_allows_files_read_by_commands(self):
        absolute = os.path.join(self.__workspace, 'src', 'a.py')
        reads = (
            _tool('c1', 'bash', {'command': 'cat src/a.py'}),
            _tool('c1', 'functions.powershell', {'command': f'Get-Content "{absolute}"'}),
            _tool('c1', 'rg', {'pattern': 'def', 'path': 'src/a.py'}),
            _tool('c1', 'grep', {'pattern': 'def', 'paths': [absolute]}),
            # Windowsでも、`/` 区切りで書いた絶対パスを読んだとみなす。
            _tool('c1', 'bash', {'command': f'cat {absolute.replace(os.sep, "/")}'}),
        )
        for read in reads:
            with self.subTest(read=read):
                events = [_user('前の依頼'), *read, _user('調べて'), _assistant('`src/a.py` はおそらく古い。')]
                self.__assert_silent(self.__invoke(events))

    def test_other_tool_arguments_do_not_count_as_reads(self):
        events = [
            _user('調べて'), *_tool('c1', 'edit', {'path': 'src/a.py'}), *_tool('c2', 'bash', {'command': 'cat src/b.py'}),
            _assistant('`src/a.py` と `src/b.py` はおそらく古い。'),
        ]
        reason = self.__assert_block(self.__invoke(events), 'src/a.py')
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
                self.__assert_silent(self.__invoke([_user('調べて'), _assistant(response)]))

    def test_resolves_paths_from_repository_root(self):
        subprocess.run(['git', 'init', '-q', self.__workspace], check=True, timeout=10)
        payload = {**self.__payload, 'cwd': os.path.join(self.__workspace, 'src')}
        self.__write_transcript([_user('調べて'), _assistant('`src/a.py` はおそらく古い。'), _agent_stop_start()])
        self.__assert_block(self.__invoke(payload=payload), 'src/a.py')

    def test_reported_files_are_limited(self):
        names = [f'f{index:02}.py' for index in range(12)]
        for name in names:
            self.__write_file(name)
        response = 'おそらく ' + ' '.join(f'`src/{name}`' for name in names) + ' は古い。'
        reason = self.__assert_block(self.__invoke([_user('調べて'), _assistant(response)]), 'src/f09.py and 2 more')
        self.assertNotIn('src/f10.py', reason)

    def test_blocks_only_once_per_request(self):
        first = [_user('調べて'), _assistant('`src/a.py` はおそらく古い。')]
        reason = self.__assert_block(self.__invoke(first))
        self.__assert_silent(self.__invoke([*first, _user(reason, 'queued'), _assistant('`src/a.py` はおそらく古い。')]))
        self.__assert_block(self.__invoke([*first, _user(reason, 'queued'), _user('次の依頼'), _assistant('`src/a.py` はたぶん古い。')]))

    def test_queued_messages_do_not_reset_the_request(self):
        events = [
            _user('調べて'), *_tool('c1', 'bash', {'command': 'cat src/a.py'}),
            _user('The final response footer is missing or incorrect.', 'queued'),
            _assistant('`src/a.py` はおそらく古い。'),
        ]
        self.__assert_silent(self.__invoke(events))

    def test_last_assistant_message_is_checked(self):
        events = [_user('調べて'), _assistant('`src/a.py` はおそらく古い。'), _assistant('`src/a.py` は未確認。')]
        self.__assert_silent(self.__invoke(events))

    def test_skips_subagents(self):
        self.__write_transcript([_user('調べて'), _assistant('`src/a.py` はおそらく古い。')])
        self.__assert_silent(self.__invoke(payload={**self.__payload, 'agentId': 'child-agent'}))

    def test_waits_for_agent_stop_event(self):
        self.__write_transcript([_user('調べて')])
        process = subprocess.Popen(
            [sys.executable, _hook_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.__environment,
        )
        process.stdin.write(json.dumps(self.__payload).encode('utf-8'))
        process.stdin.close()
        process.stdin = None
        time.sleep(0.5)
        with open(self.__transcript_path, 'a', encoding='utf-8', newline='\n') as file:
            for event in (_assistant('`src/a.py` はおそらく古い。'), _agent_stop_start()):
                file.write(json.dumps(event, ensure_ascii=False) + '\n')
        output, error = process.communicate(timeout=15)
        self.__assert_block(subprocess.CompletedProcess([], process.returncode, output, error))

    def test_custom_definitions_are_used(self):
        custom_path = os.path.join(self.__root, 'custom.json')
        with open(custom_path, 'w', encoding='utf-8-sig', newline='\n') as file:
            json.dump({'speculationPatterns': ['きっと']}, file, ensure_ascii=False)
        self.__assert_block(self.__invoke([_user('調べて'), _assistant('`src/a.py` はきっと古い。')], definitions_path=custom_path))
        self.__assert_silent(self.__invoke([_user('調べて'), _assistant('`src/a.py` はおそらく古い。')], definitions_path=custom_path))

    def test_invalid_definitions_report_error(self):
        cases = [('{', 'not valid JSON'), ('[]', 'must be a JSON object'), ('\xff', None)]
        for value in (None, [], [''], [' '], [1], 'a'):
            cases.append((json.dumps({'speculationPatterns': value}), 'non-empty array of non-empty strings'))
        cases.append((json.dumps({'speculationPatterns': ['(']}), 'Invalid regular expression'))
        self.__write_transcript([_user('調べて'), _assistant('`src/a.py` はおそらく古い。'), _agent_stop_start()])
        for index, (content, fragment) in enumerate(cases):
            with self.subTest(content=content):
                path = os.path.join(self.__root, f'definitions-{index}.json')
                with open(path, 'wb') as file:
                    file.write(content.encode('latin-1') if fragment is None else content.encode('utf-8'))
                result = self.__invoke(definitions_path=path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to load unread speculation definitions:', result.stderr)
                if fragment is not None:
                    self.assertIn(fragment.encode('utf-8'), result.stderr)
        for path in (os.path.join(self.__root, 'missing.json'), self.__root):
            with self.subTest(path=path):
                result = self.__invoke(definitions_path=path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Definitions path is not a file', result.stderr)

    def test_invalid_input_reports_error(self):
        self.__write_transcript([_user('調べて')])
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'\xff']
        for value in (None, '', ' ', 3, [], os.path.join(self.__root, 'missing.jsonl'), self.__root):
            invalid.append(json.dumps({**self.__payload, 'transcriptPath': value}).encode('utf-8'))
        for value in (None, '', 3, 'relative/path'):
            invalid.append(json.dumps({**self.__payload, 'cwd': value}).encode('utf-8'))
        for value in (None, '', 'not-a-uuid'):
            invalid.append(json.dumps({**self.__payload, 'sessionId': value}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertTrue(result.stderr)

    def test_invalid_transcript_reports_error(self):
        for content in ('{', '[]\n', '{"type":"user.message","data":[]}\n'):
            with self.subTest(content=content):
                with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
                    file.write(content)
                result = self.__invoke(payload={**self.__payload, 'timestamp': None})
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to inspect unread speculation:', result.stderr)


if __name__ == '__main__':
    _run_tests()
