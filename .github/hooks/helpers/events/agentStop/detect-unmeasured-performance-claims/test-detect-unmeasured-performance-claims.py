import json
import os
import subprocess
import sys
import tempfile
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-unmeasured-performance-claims.py')
_definitions_path = os.path.join(_script_directory, 'performance-claim-definitions.json')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


_HOOK_TIMESTAMP = 1777777777000
_REASON_PREFIX = 'The final response claims a performance improvement'


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


class DetectUnmeasuredPerformanceClaimsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-performance-claims-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__transcript_path = os.path.join(self.__root, 'events.jsonl')
        self.__payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': _HOOK_TIMESTAMP,
            'cwd': self.__root,
            'transcriptPath': self.__transcript_path,
        }

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

    def __assert_silent(self, result, message=None):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_block(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith(_REASON_PREFIX))
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_definitions_are_valid_json(self):
        with open(_definitions_path, encoding='utf-8') as file:
            definitions = json.load(file)
        for key in ('claimPatterns', 'hedgePatterns', 'measurementCommandPatterns'):
            self.assertTrue(definitions[key], key)

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        entries = configuration['hooks']['agentStop']
        relative_path = ('.github/hooks/helpers/events/agentStop/detect-unmeasured-performance-claims/'
                         'detect-unmeasured-performance-claims.py')
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_unmeasured_claims(self):
        claims = [
            'ループを辞書引きに変えて高速化した。',
            'キャッシュを入れたので速くなった。',
            '約3倍高速になった。',
            '処理時間を大幅に短縮した。',
            '不要なコピーを行わないようにしてメモリ使用量を削減した。',
            'パフォーマンスが向上した。',
            'ボトルネックを解消した。',
            'The parser is now 3x faster.',
            'This change speeds up the import.',
            'It improves performance by avoiding allocations.',
            'Reduced memory usage by streaming the file.',
        ]
        for claim in claims:
            with self.subTest(claim=claim):
                self.__assert_block(
                    self.__invoke([_user('速くして'), *_shell('c1', 'python3 -m pytest', 0), _assistant(f'対応した。\n{claim}')]),
                    claim.rstrip('。'),
                )

    def test_measured_claims_are_silent(self):
        commands = [
            'hyperfine "python3 old.py" "python3 new.py"',
            'dotnet run -c Release --project Bench',
            'go test ./... -bench=. -run=^$',
            'cargo bench',
            'python3 -m timeit -s "import calc" "calc.run()"',
            'pytest --benchmark-only',
            'time python3 main.py',
            'cd app && time npm run build',
            'Measure-Command { ./run.ps1 }',
            "python3 -c 'import time; t=time.perf_counter(); run(); print(time.perf_counter()-t)'",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.__assert_silent(self.__invoke([
                    _user('速くして'), *_shell('c1', command, 0, tool_name='powershell' if 'Measure' in command else 'bash'),
                    _assistant('3倍高速になった。'),
                ]), command)

    def test_failed_or_missing_measurements_do_not_count(self):
        cases = [
            ('failed benchmark', _shell('c1', 'cargo bench', 101)),
            ('denied benchmark', _shell('c1', 'cargo bench', None)),
            ('unrelated command', _shell('c1', 'python3 -m pytest', 0)),
        ]
        for name, events in cases:
            with self.subTest(name=name):
                self.__assert_block(self.__invoke([_user('速くして'), *events, _assistant('3倍高速になった。')]))

    def test_hedged_or_non_claims_are_silent(self):
        responses = [
            'この変更で速くなるはず。',
            '高速化した可能性がある。',
            '高速化したが、計測はしていない。',
            '処理時間は短縮されない。',
            'This should be faster, but I have not measured it.',
            'It is expected to speed up the import.',
            "It isn't faster yet.",
            '計算量をO(n^2)からO(n)にした。',
            'テストを追加した。',
            '```\n3x faster\n```\n実装した。',
            '> 高速化した\nという指摘に対応した。',
        ]
        for response in responses:
            with self.subTest(response=response):
                self.__assert_silent(self.__invoke([_user('速くして'), _assistant(response)]), response)

    def test_measurement_before_latest_request_does_not_count(self):
        self.__assert_block(self.__invoke([
            _user('計測して'), *_shell('c1', 'hyperfine "python3 main.py"', 0), _assistant('計測した。'),
            _user('速くして'), _assistant('高速化した。'),
        ]))

    def test_blocks_only_once_per_request(self):
        events = [_user('速くして'), _assistant('高速化した。')]
        reason = self.__assert_block(self.__invoke(events))
        self.__assert_silent(self.__invoke([*events, _user(reason, delivery='queued'), _assistant('高速化した。')]))
        self.__assert_block(self.__invoke([*events, _user('次'), _assistant('さらに高速化した。')]))

    def test_long_claim_is_truncated(self):
        reason = self.__assert_block(self.__invoke([_user('速くして'), _assistant('高速化した' + 'あ' * 200 + '。')]))
        self.assertIn('...', reason)

    def test_skips_when_not_applicable(self):
        with self.subTest(name='subagent'):
            self.__assert_silent(self.__invoke([_user('x'), _assistant('高速化した。')], payload={**self.__payload, 'agentId': 'a'}))
        with self.subTest(name='no final response'):
            self.__assert_silent(self.__invoke([_user('速くして')]))

    def test_invalid_input_exits_with_error(self):
        self.__invoke([_user('x'), _assistant('y')])
        cases = [
            ('empty input', b''),
            ('invalid JSON', b'{'),
            ('non-object', b'[]'),
            ('missing transcript path', json.dumps({**self.__payload, 'transcriptPath': ''}).encode('utf-8')),
            ('missing transcript', json.dumps({**self.__payload, 'transcriptPath': os.path.join(self.__root, 'none.jsonl')}).encode('utf-8')),
        ]
        for name, raw in cases:
            with self.subTest(name=name):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to detect unmeasured performance claims', result.stderr, name)
        with self.subTest(name='invalid transcript line'):
            with open(self.__transcript_path, 'w', encoding='utf-8') as file:
                file.write('not json\n')
            self.assertEqual(self.__invoke().returncode, 2)

    def test_invalid_definitions_exit_with_error(self):
        valid = {'claimPatterns': ['a'], 'hedgePatterns': ['b'], 'measurementCommandPatterns': ['c']}
        cases = [
            ('not an object', []),
            ('empty claims', {**valid, 'claimPatterns': []}),
            ('missing hedges', {key: value for key, value in valid.items() if key != 'hedgePatterns'}),
            ('invalid regex', {**valid, 'measurementCommandPatterns': ['(']}),
        ]
        self.__invoke([_user('x'), _assistant('y')])
        for name, definitions in cases:
            with self.subTest(name=name):
                path = os.path.join(self.__root, 'definitions.json')
                with open(path, 'w', encoding='utf-8') as file:
                    json.dump(definitions, file)
                self.assertEqual(self.__invoke(arguments=('--definitions-path', path)).returncode, 2, name)


if __name__ == '__main__':
    run_tests()
