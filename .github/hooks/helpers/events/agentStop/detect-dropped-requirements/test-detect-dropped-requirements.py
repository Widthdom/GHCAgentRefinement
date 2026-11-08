import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-dropped-requirements.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


_HOOK_TIMESTAMP = 1777777777000
_REQUEST = (
    '次の対応をして。\n'
    '- `block-commands` のREADMEを修正\n'
    '- ログ出力を日本語化\n'
    '- CHANGELOG.md に追記\n'
)


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


class DetectDroppedRequirementsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-dropped-requirements-test-')
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

    def __assert_silent(self, result, message=None):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_block(self, result, expected_parts=(), unexpected_parts=()):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith('The final response does not mention'))
        for part in expected_parts:
            self.assertIn(part, output['reason'])
        for part in unexpected_parts:
            self.assertNotIn(part, output['reason'])
        return output['reason']

    def test_blocks_when_an_item_is_not_mentioned(self):
        reason = self.__assert_block(
            self.__invoke([_user(_REQUEST), _assistant('block-commandsのREADMEを直し、ログを日本語にした。')]),
            ['1 of the 3 items', '"CHANGELOG.md に追記"', 'Do not drop a requested item silently.'],
            ['block-commands', 'ログ出力'],
        )
        self.assertIn('state explicitly', reason)

    def test_allows_when_every_item_is_mentioned(self):
        cases = (
            ('all mentioned', 'block-commandsのREADMEを直した。ログ出力を日本語化した。CHANGELOG.mdにも追記した。'),
            ('case insensitive', 'BLOCK-COMMANDS と changelog.md を更新し、ログも直した。'),
            ('reported as not done', 'block-commandsとログは対応した。CHANGELOG.mdは存在しないため追記していない。'),
        )
        for name, response in cases:
            with self.subTest(name=name):
                self.__assert_silent(self.__invoke([_user(_REQUEST), _assistant(response)]), name)

    def test_list_formats(self):
        cases = (
            ('numbered', '1. `alpha.py` を直す\n2. `beta.py` を直す\n'),
            ('parenthesis', '1) alpha module\n2) beta module\n'),
            ('full-width parenthesis', '1）アルファ\n2）ベータ\n'),
            ('circled', '① アルファ\n② ベータ\n'),
            ('asterisk', '* alpha module\n* beta module\n'),
            ('middle dot', '・アルファ\n・ベータ\n'),
        )
        for name, request in cases:
            with self.subTest(name=name):
                self.__assert_block(self.__invoke([_user(request), _assistant('何もしていない。')]), ['2 of the 2 items'])

    def test_nested_items_are_details_of_the_parent(self):
        request = '- `alpha.py` を直す\n  - 細かい条件Ａ\n- `beta.py` を直す\n'
        self.__assert_silent(self.__invoke([_user(request), _assistant('alpha.py と beta.py を直した。')]))

    def test_ignores_requests_without_a_list(self):
        cases = (
            ('no list', 'alpha.pyとbeta.pyを直して。'),
            ('single item', '- `alpha.py` を直す\n'),
            ('list in code block', '```\n- alpha\n- beta\n```\nこれを実行して'),
            ('only stop words', '- 追加\n- 修正\n'),
            ('short ascii only', '- ab\n- cd\n'),
        )
        for name, request in cases:
            with self.subTest(name=name):
                self.__assert_silent(self.__invoke([_user(request), _assistant('完了した。')]), name)

    def test_latest_request_is_used(self):
        events = [_user(_REQUEST), _assistant('途中。'), _user('ありがとう'), _assistant('どういたしまして。')]
        self.__assert_silent(self.__invoke(events), 'Items from an earlier request must be ignored.')

    def test_blocks_only_once_per_request(self):
        events = [
            _user(_REQUEST), _assistant('一部完了。'),
            _user('The final response does not mention 3 of the 3 items ...', delivery='queued'),
            _assistant('まだ一部。'),
        ]
        self.__assert_silent(self.__invoke(events))
        events.extend([_user('- `gamma.py`\n- `delta.py`\n'), _assistant('何もしていない。')])
        self.__assert_block(self.__invoke(events), ['gamma.py', 'delta.py'])

    def test_lists_at_most_five_items(self):
        request = ''.join(f'- `item{index}.py`\n' for index in range(7))
        self.__assert_block(
            self.__invoke([_user(request), _assistant('何もしていない。')]),
            ['7 of the 7 items', 'and 2 more'], ['item5.py'],
        )

    def test_skips_without_request_or_response_and_subagents(self):
        self.__assert_silent(self.__invoke([_user(_REQUEST)]), 'No final response.')
        self.__assert_silent(self.__invoke([_assistant('こんにちは')]), 'No user request.')
        self.__write_transcript([_user(_REQUEST), _assistant('何もしていない。'), _agent_stop_start()])
        self.__assert_silent(self.__invoke(payload={**self.__payload, 'agentId': 'sub-1'}), 'Subagents are ignored.')

    def test_invalid_input(self):
        self.__write_transcript([_agent_stop_start()])
        cases = (
            ('empty', b'', b'Failed to inspect dropped requirements: hook input is empty.'),
            ('not json', b'{', b'Failed to inspect dropped requirements: Expecting property name'),
            ('invalid utf-8', b'\xff', b"Failed to inspect dropped requirements: 'utf-8' codec can't decode"),
            ('not object', b'[]', b'Failed to inspect dropped requirements: hook input must be a JSON object.'),
            ('no transcript', json.dumps({'timestamp': _HOOK_TIMESTAMP}).encode('utf-8'),
             b'transcriptPath must be a non-empty string.'),
            ('missing transcript', json.dumps({'transcriptPath': os.path.join(self.__root, 'none.jsonl')}).encode('utf-8'),
             b'transcript path is not a file'),
        )
        for name, raw, message in cases:
            with self.subTest(name=name):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(message, result.stderr, name)

    def test_invalid_transcript(self):
        with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write('{"type":"user.message","data":[]}\n')
        result = self.__invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'Failed to inspect dropped requirements:', result.stderr)

    def test_invalid_definitions(self):
        self.__write_transcript([_user(_REQUEST), _assistant('完了。'), _agent_stop_start()])
        for name, content in (
            ('missing file', None),
            ('not json', '{'),
            ('not object', '[]'),
            ('missing stop words', {}),
            ('empty stop words', {'stopWords': []}),
            ('blank stop word', {'stopWords': [' ']}),
        ):
            with self.subTest(name=name):
                path = os.path.join(self.__root, f'definitions-{name.replace(" ", "-")}.json')
                if content is not None:
                    with open(path, 'w', encoding='utf-8', newline='\n') as file:
                        file.write(content if isinstance(content, str) else json.dumps(content))
                result = self.__invoke(definitions_path=path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to load requirement definitions:', result.stderr, name)


if __name__ == '__main__':
    _run_tests()
