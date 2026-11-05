import json
import os
import subprocess
import sys
import tempfile
import time
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-rerun-until-green.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


_HOOK_TIMESTAMP = 1777777777000
_REASON_PREFIX = 'A test command passed only after being re-run without any change'


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


def _edit(call_id, arguments, tool_name='edit', success=True):
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': tool_name, 'arguments': arguments}},
        {'type': 'tool.execution_complete', 'data': {'toolCallId': call_id, 'success': success}},
    ]


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


class DetectRerunUntilGreenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-rerun-until-green-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = temporary.name
        self.__transcript_path = os.path.join(self.__root, 'events.jsonl')
        self.__payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': _HOOK_TIMESTAMP,
            'cwd': self.__root,
            'transcriptPath': self.__transcript_path,
        }

    def __write_transcript(self, events):
        with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            for event in events:
                file.write(json.dumps(event, ensure_ascii=False) + '\n')

    def __invoke(self, events=None, payload=None, raw=None):
        if events is not None:
            self.__write_transcript([*events, _agent_stop_start()])
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_block(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith(_REASON_PREFIX))
        self.assertIn('Investigate why the earlier run failed', output['reason'])
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        self.assertEqual(configuration['version'], 1)
        entries = configuration['hooks']['agentStop']
        relative_path = '.github/hooks/helpers/events/agentStop/detect-rerun-until-green/detect-rerun-until-green.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_pass_after_unchanged_rerun(self):
        for tool_name in ('bash', 'powershell', 'functions.bash'):
            for claim in ('テストはすべて通過した。', 'All tests pass.', '「テスト0件成功」は引用。テストが通った。'):
                with self.subTest(tool_name=tool_name, claim=claim):
                    events = [
                        _user('直して'), *_edit('e1', {'path': 'src/a.py'}),
                        *_shell('c1', 'npm test', 1, tool_name), *_shell('c2', 'npm test', 0, tool_name), _assistant(claim),
                    ]
                    self.__assert_block(self.__invoke(events), '(npm test)', 'failed once')

    def test_counts_consecutive_failures(self):
        events = [
            _user('直して'), *_shell('c1', 'pytest  -q', 1), *_shell('c2', 'pytest -q', 2),
            *_shell('c3', 'pytest -q', 0), *_shell('c4', ' pytest -q ', 0), _assistant('テストが通った。'),
        ]
        self.__assert_block(self.__invoke(events), 'failed 2 times')

    def test_allows_when_something_changed_between_runs(self):
        separators = (
            _edit('e1', {'path': 'src/a.py', 'old_str': 'a', 'new_str': 'b'}),
            _edit('e1', {'path': 'README.md', 'file_text': 'x'}, 'create'),
            _edit('e1', {'input': '*** Begin Patch\n*** Delete File: src/old.py\n*** End Patch'}, 'apply_patch'),
            _shell('s1', 'pip install -r requirements.txt', 0),
            _shell('s1', 'pytest tests/test_a.py', 1),
            _shell('s1', 'pytest', None),
        )
        for separator in separators:
            with self.subTest(separator=separator):
                events = [
                    _user('直して'), *_shell('c1', 'pytest', 1), *separator,
                    *_shell('c2', 'pytest', 0), _assistant('テストが通った。'),
                ]
                self.__assert_silent(self.__invoke(events))

    def test_ignores_failed_and_viewing_edits_between_runs(self):
        cases = (
            _edit('e1', {'path': 'src/a.py'}, success=False),
            _edit('e1', {'command': 'view', 'path': 'src/a.py'}, 'str_replace_editor'),
            _edit('e1', {'path': 'src/a.py'}, 'view'),
        )
        for between in cases:
            with self.subTest(between=between):
                events = [
                    _user('直して'), *_shell('c1', 'pytest', 1), *between,
                    *_shell('c2', 'pytest', 0), _assistant('テストが通った。'),
                ]
                self.__assert_block(self.__invoke(events))

    def test_ignores_when_last_test_did_not_pass_or_never_failed(self):
        cases = (
            [*_shell('c1', 'npm test', 0)],
            [*_shell('c1', 'npm test', 0), *_shell('c2', 'npm test', 0)],
            [*_shell('c1', 'npm test', 0), *_shell('c2', 'npm test', 1)],
            [*_shell('c1', 'npm test', 1), *_shell('c2', 'npm test', None)],
            [*_shell('c1', 'npm test', 1), *_shell('c2', 'npm test', 0), *_shell('c3', 'npm test', 1)],
            [*_shell('c1', 'npm run build', 1), *_shell('c2', 'npm run build', 0)],
            [],
        )
        for runs in cases:
            with self.subTest(runs=runs):
                self.__assert_silent(self.__invoke([_user('直して'), *runs, _assistant('テストが通った。')]))

    def test_ignores_responses_without_claim_or_with_flaky_note(self):
        responses = (
            'テストは通っていない。',
            'The tests did not pass.',
            '結果:\n```\nAll tests passed\n```\n',
            '「テスト0件成功」は未確認の条件。',
            '`テストが通った` は診断用の表現。',
            '> All tests passed.\nこれは引用。',
            'テストは通ったが、1回目は失敗しており不安定なテストと考えられる。',
            'Tests pass, but the suite looks flaky: the first run failed.',
        )
        for response in responses:
            with self.subTest(response=response):
                events = [_user('直して'), *_shell('c1', 'npm test', 1), *_shell('c2', 'npm test', 0), _assistant(response)]
                self.__assert_silent(self.__invoke(events))

    def test_flaky_note_inside_code_block_does_not_count(self):
        response = 'テストが通った。\n```\n# flaky\n```\n'
        events = [_user('直して'), *_shell('c1', 'npm test', 1), *_shell('c2', 'npm test', 0), _assistant(response)]
        self.__assert_block(self.__invoke(events))

    def test_long_command_is_truncated(self):
        command = 'pytest ' + 'x' * 300
        events = [_user('直して'), *_shell('c1', command, 1), *_shell('c2', command, 0), _assistant('Tests passed.')]
        reason = self.__assert_block(self.__invoke(events))
        self.assertIn(command[:200] + '...', reason)
        self.assertNotIn(command, reason)

    def test_only_events_after_latest_user_request_count(self):
        events = [
            _user('前の依頼'), *_shell('c1', 'npm test', 1),
            _user('次の依頼'), *_shell('c2', 'npm test', 0), _assistant('テストが通った。'),
        ]
        self.__assert_silent(self.__invoke(events))

    def test_blocks_only_once_per_request(self):
        first = [_user('直して'), *_shell('c1', 'npm test', 1), *_shell('c2', 'npm test', 0), _assistant('テストが通った。')]
        reason = self.__assert_block(self.__invoke(first))
        self.__assert_silent(self.__invoke([*first, _user(reason, 'queued'), _assistant('テストが通った。')]))
        self.__assert_block(self.__invoke([*first, _user('割り込み', 'queued'), _assistant('テストが通った。')]))

    def test_skips_subagents(self):
        self.__write_transcript([_user('直して'), *_shell('c1', 'npm test', 1), *_shell('c2', 'npm test', 0), _assistant('テストが通った。')])
        self.__assert_silent(self.__invoke(payload={**self.__payload, 'agentId': 'child-agent'}))

    def test_waits_for_agent_stop_event(self):
        self.__write_transcript([_user('直して'), *_shell('c1', 'npm test', 1), *_shell('c2', 'npm test', 0)])
        process = subprocess.Popen(
            [sys.executable, _hook_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        process.stdin.write(json.dumps(self.__payload).encode('utf-8'))
        process.stdin.close()
        process.stdin = None
        time.sleep(0.5)
        with open(self.__transcript_path, 'a', encoding='utf-8', newline='\n') as file:
            for event in (_assistant('テストが通った。'), _agent_stop_start()):
                file.write(json.dumps(event, ensure_ascii=False) + '\n')
        output, error = process.communicate(timeout=15)
        self.__assert_block(subprocess.CompletedProcess([], process.returncode, output, error))

    def test_invalid_input_reports_error(self):
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'\xff']
        for value in (None, '', ' ', 3, [], os.path.join(self.__root, 'missing.jsonl'), self.__root):
            invalid.append(json.dumps({**self.__payload, 'transcriptPath': value}).encode('utf-8'))
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
                self.assertIn(b'Failed to inspect test re-runs:', result.stderr)


if __name__ == '__main__':
    run_tests()
