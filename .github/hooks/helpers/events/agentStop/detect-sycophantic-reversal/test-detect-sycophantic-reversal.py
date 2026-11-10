import json
import os
import runpy
import subprocess
import sys
import tempfile
import time
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-sycophantic-reversal.py')
_definitions_path = os.path.join(_script_directory, 'sycophantic-reversal-definitions.json')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


_HOOK_TIMESTAMP = 1777777777000


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _tool(call_id, tool_name):
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': tool_name, 'arguments': {}}},
        {'type': 'tool.execution_complete', 'data': {'toolCallId': call_id, 'success': True}},
    ]


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


def _pushback_turn(pushback='それ違くない？', response='ご指摘の通り、誤りだった。'):
    return [_user('このAPIは非推奨？'), _assistant('非推奨ではない。'), _user(pushback), _assistant(response)]


class DetectSycophanticReversalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-sycophantic-reversal-test-')
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

    def __invoke(self, events=None, payload=None, raw=None, definitions_path=None):
        if events is not None:
            self.__write_transcript([*events, _agent_stop_start()])
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        options = [] if definitions_path is None else ['--definitions-path', definitions_path]
        return subprocess.run(
            [sys.executable, _hook_path, *options], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_block(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith('The final response reverses'))
        self.assertIn('Do not change your position only because the user disagreed.', output['reason'])
        return output['reason']

    def test_blocks_reversal_without_evidence(self):
        cases = (
            ('それ違くない？', 'ご指摘の通り、誤りだった。'),
            ('それは違うのでは？', 'ご指摘の通り、誤りだった。'),
            ('本当に？', 'おっしゃるとおり、非推奨だった。'),
            ('間違ってるよ', '私が間違っていた。訂正する。'),
            ('非推奨のはずでは', '勘違いしていた。'),
            ('Are you sure?', "You're absolutely right, it is deprecated."),
            ("That's wrong.", 'My mistake. I stand corrected.'),
            ('それ違くない？', 'Good catch.'),
        )
        for pushback, response in cases:
            with self.subTest(pushback=pushback, response=response):
                self.__assert_block(self.__invoke(_pushback_turn(pushback, response)))

    def test_allows_reversal_after_evidence(self):
        for tool_name in ('view', 'rg', 'bash', 'functions.powershell', 'web_fetch', 'task'):
            with self.subTest(tool_name=tool_name):
                events = _pushback_turn()
                events[3:3] = _tool('c1', tool_name)
                self.__assert_silent(self.__invoke(events))

    def test_non_evidence_tools_do_not_count(self):
        for tool_name in ('edit', 'apply_patch', 'ask_user', 'task_complete', 'skill'):
            with self.subTest(tool_name=tool_name):
                events = _pushback_turn()
                events[3:3] = _tool('c1', tool_name)
                self.__assert_block(self.__invoke(events))

    def test_evidence_before_latest_request_does_not_count(self):
        events = _pushback_turn()
        events[1:1] = _tool('c1', 'view')
        self.__assert_block(self.__invoke(events))

    def test_ignores_without_pushback_or_reversal(self):
        cases = (
            ('ありがとう', 'ご指摘の通り、修正した。'),
            ('それ違くない？', '確認した上で、非推奨ではないと判断する。'),
            ('Thanks.', "You're right."),
            ('```\nwrong\n```\nこれを見て', 'ご指摘の通り。'),
            ('本当に？', '```\nYou are right\n```'),
        )
        for pushback, response in cases:
            with self.subTest(pushback=pushback, response=response):
                self.__assert_silent(self.__invoke(_pushback_turn(pushback, response)))

    def test_ignores_first_request_without_prior_response(self):
        self.__assert_silent(self.__invoke([_user('それ違くない？'), _assistant('ご指摘の通り。')]))

    def test_queued_messages_do_not_reset_the_request(self):
        events = _pushback_turn()
        events.insert(3, _user('The final response footer is missing or incorrect.', 'queued'))
        self.__assert_block(self.__invoke(events))

    def test_blocks_only_once_per_request(self):
        first = _pushback_turn()
        reason = self.__assert_block(self.__invoke(first))
        self.__assert_silent(self.__invoke([*first, _user(reason, 'queued'), _assistant('ご指摘の通り。')]))
        self.__assert_block(self.__invoke([*first, _user(reason, 'queued'), _user('違うよ'), _assistant('ご指摘の通り。')]))

    def test_last_assistant_message_is_checked(self):
        events = [*_pushback_turn(), _assistant('いや、確認すると非推奨ではない。')]
        self.__assert_silent(self.__invoke(events))

    def test_skips_subagents(self):
        self.__write_transcript(_pushback_turn())
        self.__assert_silent(self.__invoke(payload={**self.__payload, 'agentId': 'child-agent'}))

    def test_waits_for_agent_stop_event(self):
        self.__write_transcript(_pushback_turn()[:3])

        process = subprocess.Popen(
            [sys.executable, _hook_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        process.stdin.write(json.dumps(self.__payload).encode('utf-8'))
        process.stdin.close()
        process.stdin = None
        time.sleep(0.5)
        with open(self.__transcript_path, 'a', encoding='utf-8', newline='\n') as file:
            for event in (_assistant('ご指摘の通り。'), _agent_stop_start()):
                file.write(json.dumps(event, ensure_ascii=False) + '\n')
        output, error = process.communicate(timeout=15)
        self.__assert_block(subprocess.CompletedProcess([], process.returncode, output, error))

    def test_definitions_json_is_valid(self):
        with open(_definitions_path, 'rb') as file:
            content = file.read()
        self.assertFalse(content.startswith(b'\xef\xbb\xbf'))
        definitions = json.loads(content.decode('utf-8'))
        self.assertEqual(set(definitions), {'evidenceToolNames', 'pushbackPatterns', 'reversalPatterns'})

    def test_custom_definitions_are_used(self):
        custom_path = os.path.join(self.__root, 'custom.json')
        with open(custom_path, 'w', encoding='utf-8-sig', newline='\n') as file:
            json.dump({
                'evidenceToolNames': ['inspect'],
                'pushbackPatterns': ['ちがうね'],
                'reversalPatterns': ['そうだった'],
            }, file, ensure_ascii=False)
        self.__assert_block(self.__invoke(_pushback_turn('ちがうね', 'そうだった'), definitions_path=custom_path))
        self.__assert_silent(self.__invoke(_pushback_turn(), definitions_path=custom_path))
        events = _pushback_turn('ちがうね', 'そうだった')
        events[3:3] = _tool('c1', 'inspect')
        self.__assert_silent(self.__invoke(events, definitions_path=custom_path))
        events[3:5] = _tool('c1', 'view')
        self.__assert_block(self.__invoke(events, definitions_path=custom_path))

    def test_invalid_definitions_report_error(self):
        valid = {'evidenceToolNames': ['view'], 'pushbackPatterns': ['a'], 'reversalPatterns': ['b']}
        cases = [
            ('{', 'not valid JSON'),
            ('[]', 'must be a JSON object'),
            ('\xff', None),
        ]
        for key in valid:
            for value in (None, [], [''], [' '], [1], 'a'):
                cases.append((json.dumps({**valid, key: value}), f'non-empty array of non-empty strings for {key}'))
        for key in ('pushbackPatterns', 'reversalPatterns'):
            cases.append((json.dumps({**valid, key: ['(']}), f'invalid regular expression in {key}'))
        self.__write_transcript([*_pushback_turn(), _agent_stop_start()])
        for index, (content, fragment) in enumerate(cases):
            with self.subTest(content=content):
                path = os.path.join(self.__root, f'definitions-{index}.json')
                with open(path, 'wb') as file:
                    file.write(content.encode('latin-1') if fragment is None else content.encode('utf-8'))
                result = self.__invoke(definitions_path=path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to load sycophantic reversal definitions:', result.stderr)
                if fragment is not None:
                    self.assertIn(fragment.encode('utf-8'), result.stderr)
        for path in (os.path.join(self.__root, 'missing.json'), self.__root):
            with self.subTest(path=path):
                result = self.__invoke(definitions_path=path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Definitions path is not a file', result.stderr)

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
                self.assertIn(b'Failed to inspect sycophantic reversal:', result.stderr)


if __name__ == '__main__':
    _run_tests()
