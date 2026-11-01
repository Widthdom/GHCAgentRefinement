import json
import os
import subprocess
import sys
import tempfile
import time
import unittest


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'detect-stale-verification.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


HOOK_TIMESTAMP = 1777777777000
REASON_PREFIX = 'Files were edited after the last passing test run'


def user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def shell(call_id, command, exit_code, tool_name='bash'):
    complete = {'toolCallId': call_id, 'success': exit_code is not None}
    if exit_code is not None:
        complete['shellExecution'] = {'exitCode': exit_code}
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': tool_name, 'arguments': {'command': command}}},
        {'type': 'tool.execution_complete', 'data': complete},
    ]


def edit(call_id, arguments, tool_name='edit', success=True):
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': tool_name, 'arguments': arguments}},
        {'type': 'tool.execution_complete', 'data': {'toolCallId': call_id, 'success': success}},
    ]


def agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': HOOK_TIMESTAMP}}}


class DetectStaleVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-stale-verification-test-')
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.transcript_path = os.path.join(self.root, 'events.jsonl')
        self.payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': HOOK_TIMESTAMP,
            'cwd': self.root,
            'transcriptPath': self.transcript_path,
        }

    def write_transcript(self, events):
        with open(self.transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            for event in events:
                file.write(json.dumps(event, ensure_ascii=False) + '\n')

    def invoke(self, events=None, payload=None, raw=None):
        if events is not None:
            self.write_transcript([*events, agent_stop_start()])
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_block(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith(REASON_PREFIX))
        self.assertIn('Re-run the relevant tests now', output['reason'])
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        self.assertEqual(configuration['version'], 1)
        entries = configuration['hooks']['agentStop']
        relative_path = '.github/hooks/helpers/events/agentStop/detect-stale-verification/detect-stale-verification.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_edits_after_passing_test(self):
        edits = (
            (edit('e1', {'path': os.path.join(self.root, 'src', 'app.py'), 'old_str': 'a', 'new_str': 'b'}), 'src/app.py'),
            (edit('e1', {'path': 'src/app.ts', 'file_text': 'x'}, 'create'), 'src/app.ts'),
            (edit('e1', {'command': 'str_replace', 'path': 'lib/a.go'}, 'str_replace_editor'), 'lib/a.go'),
            (edit('e1', {'input': '*** Begin Patch\n*** Update File: pkg/b.cs\n@@\n-a\n+b\n*** End Patch'}, 'apply_patch'), 'pkg/b.cs'),
            (edit('e1', '{"path": "src/json.py"}', 'functions.edit'), 'src/json.py'),
        )
        for edit_events, fragment in edits:
            for claim in ('テストはすべて通過した。', 'All tests pass.'):
                with self.subTest(fragment=fragment, claim=claim):
                    events = [user('直して'), *shell('c1', 'npm test', 0), *edit_events, assistant(claim)]
                    reason = self.assert_block(self.invoke(events), f'- {fragment}')
                    self.assertIn('(npm test)', reason)

    def test_allows_when_tests_rerun_after_edit(self):
        events = [
            user('直して'), *shell('c1', 'pytest', 0), *edit('e1', {'path': 'src/a.py'}),
            *shell('c2', 'pytest -q', 0), assistant('テストが通った。'),
        ]
        self.assert_silent(self.invoke(events))

    def test_ignores_non_test_commands_after_edit(self):
        events = [
            user('直して'), *shell('c1', 'pytest', 0), *edit('e1', {'path': 'src/a.py'}),
            *shell('c2', 'git diff', 0), assistant('テストが通った。'),
        ]
        self.assert_block(self.invoke(events), '- src/a.py')

    def test_ignores_documents_hooks_and_failed_or_viewed_edits(self):
        cases = (
            edit('e1', {'path': 'README.md'}),
            edit('e1', {'path': 'docs/guide.RST'}),
            edit('e1', {'path': 'notes.txt'}),
            edit('e1', {'path': '.github/hooks/helpers/events/agentStop/x/x.py'}),
            edit('e1', {'path': 'src/a.py'}, success=False),
            edit('e1', {'command': 'view', 'path': 'src/a.py'}, 'str_replace_editor'),
        )
        for edit_events in cases:
            with self.subTest(edit_events=edit_events):
                events = [user('直して'), *shell('c1', 'npm test', 0), *edit_events, assistant('テストが通った。')]
                self.assert_silent(self.invoke(events))

    def test_leaves_failed_or_missing_test_runs_to_other_hook(self):
        cases = (
            [*edit('e1', {'path': 'src/a.py'})],
            [*shell('c1', 'npm test', 1), *edit('e1', {'path': 'src/a.py'})],
            [*shell('c1', 'npm test', 0), *edit('e1', {'path': 'src/a.py'}), *shell('c2', 'npm test', None)],
        )
        for runs in cases:
            with self.subTest(runs=runs):
                self.assert_silent(self.invoke([user('直して'), *runs, assistant('テストが通った。')]))

    def test_ignores_responses_without_test_claim(self):
        responses = (
            'テストは通っていない。',
            '修正した。テストは再実行していない。',
            'The tests did not pass.',
            '結果:\n```\nAll tests passed\n```\n',
        )
        for response in responses:
            with self.subTest(response=response):
                events = [user('直して'), *shell('c1', 'npm test', 0), *edit('e1', {'path': 'src/a.py'}), assistant(response)]
                self.assert_silent(self.invoke(events))

    def test_lists_unique_files_with_limit(self):
        edit_events = []
        for index in range(12):
            edit_events.extend(edit(f'e{index}', {'path': f'src/f{index:02}.py'}))
        edit_events.extend(edit('dup', {'path': 'src/f00.py'}))
        events = [user('直して'), *shell('c1', 'npm test', 0), *edit_events, assistant('テストが通った。')]
        reason = self.assert_block(self.invoke(events), '- src/f09.py', '... and 2 more')
        self.assertNotIn('src/f10.py', reason)
        self.assertEqual(reason.count('src/f00.py'), 1)

    def test_long_command_is_truncated(self):
        command = 'pytest ' + 'x' * 300
        events = [user('直して'), *shell('c1', command, 0), *edit('e1', {'path': 'src/a.py'}), assistant('Tests passed.')]
        reason = self.assert_block(self.invoke(events))
        self.assertIn(command[:200] + '...', reason)
        self.assertNotIn(command, reason)

    def test_only_events_after_latest_user_request_count(self):
        events = [
            user('前の依頼'), *shell('c1', 'npm test', 0), *edit('e1', {'path': 'src/a.py'}), assistant('テストが通った。'),
            user('次の依頼'), *shell('c2', 'npm test', 0), assistant('テストが通った。'),
        ]
        self.assert_silent(self.invoke(events))

    def test_blocks_only_once_per_request(self):
        first = [user('直して'), *shell('c1', 'npm test', 0), *edit('e1', {'path': 'src/a.py'}), assistant('テストが通った。')]
        reason = self.assert_block(self.invoke(first))
        self.assert_silent(self.invoke([*first, user(reason, 'queued'), assistant('テストが通った。')]))
        self.assert_silent(self.invoke([*first, user('割り込み', 'queued'), *shell('c2', 'npm test', 0), assistant('テストが通った。')]))

    def test_skips_subagents(self):
        self.write_transcript([user('直して'), *shell('c1', 'npm test', 0), *edit('e1', {'path': 'src/a.py'}), assistant('テストが通った。')])
        self.assert_silent(self.invoke(payload={**self.payload, 'agentId': 'child-agent'}))

    def test_waits_for_agent_stop_event(self):
        self.write_transcript([user('直して'), *shell('c1', 'npm test', 0), *edit('e1', {'path': 'src/a.py'})])
        process = subprocess.Popen(
            [sys.executable, hook_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        process.stdin.write(json.dumps(self.payload).encode('utf-8'))
        process.stdin.close()
        process.stdin = None
        time.sleep(0.5)
        with open(self.transcript_path, 'a', encoding='utf-8', newline='\n') as file:
            for event in (assistant('テストが通った。'), agent_stop_start()):
                file.write(json.dumps(event, ensure_ascii=False) + '\n')
        output, error = process.communicate(timeout=15)
        self.assert_block(subprocess.CompletedProcess([], process.returncode, output, error), '- src/a.py')

    def test_invalid_input_reports_error(self):
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'\xff']
        for value in (None, '', ' ', 3, [], os.path.join(self.root, 'missing.jsonl'), self.root):
            invalid.append(json.dumps({**self.payload, 'transcriptPath': value}).encode('utf-8'))
        for value in (None, '', 'relative/path', 3):
            invalid.append(json.dumps({**self.payload, 'cwd': value}).encode('utf-8'))
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
                self.assertIn(b'Failed to inspect stale verification:', result.stderr)


if __name__ == '__main__':
    run_tests()
