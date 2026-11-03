import json
import os
import subprocess
import sys
import tempfile
import time
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'verify-completion-claims.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


_HOOK_TIMESTAMP = 1777777777000


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _shell(call_id, command, exit_code, tool_name='bash'):
    complete = {'toolCallId': call_id, 'success': exit_code is not None}
    if exit_code is not None:
        complete['shellExecution'] = {'exitCode': exit_code}
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': tool_name, 'arguments': {'command': command}}},
        {'type': 'tool.execution_complete', 'data': complete},
    ]


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


class VerifyCompletionClaimsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-completion-claims-test-')
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.transcript_path = os.path.join(self.root, 'events.jsonl')
        self.payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': _HOOK_TIMESTAMP,
            'cwd': self.root,
            'transcriptPath': self.transcript_path,
        }

    def write_transcript(self, events):
        with open(self.transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            for event in events:
                file.write(json.dumps(event, ensure_ascii=False) + '\n')

    def invoke(self, events=None, payload=None, raw=None):
        if events is not None:
            self.write_transcript([*events, _agent_stop_start()])
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
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
        self.assertTrue(output['reason'].startswith('The final response claims'))
        self.assertIn('Do not claim success without evidence.', output['reason'])
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        self.assertEqual(configuration['version'], 1)
        entries = configuration['hooks']['agentStop']
        relative_path = '.github/hooks/helpers/events/agentStop/verify-completion-claims/verify-completion-claims.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_test_claim_without_test_run(self):
        claims = (
            'テストは全部通った。',
            '新しいhookのテストは15件すべて通過した。',
            'テストが成功しました。',
            'All tests pass.',
            'The tests are now passing.',
        )
        for claim in claims:
            with self.subTest(claim=claim):
                events = [_user('直して'), *_shell('c1', 'git status', 0), _assistant(claim)]
                self.assert_block(self.invoke(events), 'no test command was run')

    def test_blocks_when_last_test_run_failed(self):
        cases = (
            ([*_shell('c1', 'python3 test-a.py', 1)], 'exited with code 1: python3 test-a.py'),
            ([*_shell('c1', 'npm test', 0), *_shell('c2', 'pytest -q', 2)], 'exited with code 2: pytest -q'),
            ([*_shell('c1', 'go test ./...', None)], 'did not complete: go test ./...'),
        )
        for runs, fragment in cases:
            with self.subTest(fragment=fragment):
                events = [_user('直して'), *runs, _assistant('テストが通った。')]
                self.assert_block(self.invoke(events), fragment)

    def test_long_failed_command_is_truncated(self):
        command = 'pytest ' + 'x' * 300
        events = [_user('直して'), *_shell('c1', command, 1), _assistant('Tests passed.')]
        reason = self.assert_block(self.invoke(events))
        self.assertIn(command[:200] + '...', reason)
        self.assertNotIn(command, reason)

    def test_allows_test_claim_with_passing_last_run(self):
        runs = (
            _shell('c1', 'python3 .github/hooks/tests/test-all-hooks.py', 0),
            _shell('c1', 'Invoke-Pester', 0, 'functions.powershell'),
            [*_shell('c1', 'npm test', 1), *_shell('c2', 'npm test', 0), *_shell('c3', 'grep x y', 1)],
        )
        for events in runs:
            with self.subTest(events=events):
                self.assert_silent(self.invoke([_user('直して'), *events, _assistant('テストはすべて通過した。')]))

    def test_verification_claim_requires_any_command(self):
        claims = ('動作確認済み。', '機能することを確認した。', 'I verified that it works.')
        for claim in claims:
            with self.subTest(claim=claim):
                self.assert_block(self.invoke([_user('直して'), _assistant(claim)]), 'behavior was verified')
                self.assert_silent(self.invoke([_user('直して'), *_shell('c1', 'ls', 0), _assistant(claim)]))

    def test_ignores_non_claims(self):
        responses = (
            'テストは通っていない。',
            'テストが成功しなかった。',
            'Windows上で実際に動かしてはいない。',
            'テストを追加した。',
            'The tests did not pass.',
            'latest version passed review.',
            '結果:\n```\nAll tests passed\n```\n',
            '',
        )
        for response in responses:
            with self.subTest(response=response):
                self.assert_silent(self.invoke([_user('直して'), _assistant(response)]))

    def test_only_runs_after_latest_user_request_count(self):
        events = [
            _user('前の依頼'), *_shell('c1', 'npm test', 0), _assistant('テストが通った。'),
            _user('次の依頼'), _assistant('テストが通った。'),
        ]
        self.assert_block(self.invoke(events), 'no test command was run')

    def test_queued_messages_do_not_reset_the_request(self):
        events = [
            _user('直して'), *_shell('c1', 'npm test', 0),
            _user('The final response footer is missing or incorrect.', 'queued'),
            _assistant('テストが通った。'),
        ]
        self.assert_silent(self.invoke(events))

    def test_blocks_only_once_per_request(self):
        first = [_user('直して'), _assistant('テストが通った。')]
        reason = self.assert_block(self.invoke(first))
        self.assert_silent(self.invoke([*first, _user(reason, 'queued'), _assistant('テストが通った。')]))
        self.assert_block(self.invoke([*first, _user(reason, 'queued'), _user('次の依頼'), _assistant('テストが通った。')]))

    def test_last_assistant_message_is_checked(self):
        events = [_user('直して'), _assistant('テストが通った。'), _assistant('テストはまだ実行していない。')]
        self.assert_silent(self.invoke(events))

    def test_skips_subagents(self):
        events = [_user('直して'), _assistant('テストが通った。')]
        self.write_transcript(events)
        self.assert_silent(self.invoke(payload={**self.payload, 'agentId': 'child-agent'}))

    def test_waits_for_agent_stop_event(self):
        self.write_transcript([_user('直して')])

        def append_later():
            time.sleep(0.5)
            with open(self.transcript_path, 'a', encoding='utf-8', newline='\n') as file:
                for event in (_assistant('テストが通った。'), _agent_stop_start()):
                    file.write(json.dumps(event, ensure_ascii=False) + '\n')

        process = subprocess.Popen(
            [sys.executable, _hook_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        process.stdin.write(json.dumps(self.payload).encode('utf-8'))
        process.stdin.close()
        process.stdin = None
        append_later()
        output, error = process.communicate(timeout=15)
        self.assert_block(subprocess.CompletedProcess([], process.returncode, output, error))

    def test_invalid_input_reports_error(self):
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'\xff']
        for value in (None, '', ' ', 3, [], os.path.join(self.root, 'missing.jsonl'), self.root):
            invalid.append(json.dumps({**self.payload, 'transcriptPath': value}).encode('utf-8'))
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
                self.assertIn(b'Failed to inspect completion claims:', result.stderr)


if __name__ == '__main__':
    run_tests()
