import json
import os
import runpy
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_utility = runpy.run_path(os.path.join(_script_directory, 'get-turn-events.py'))

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


_REASON_PREFIX = 'Hook reason'


def _user(content, queued=False):
    data = {'content': content}
    if queued:
        data['delivery'] = 'queued'
    return {'type': 'user.message', 'data': data}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


def _tool_start(tool_name):
    return {'type': 'tool.execution_start', 'data': {'toolName': tool_name}}


class GetTurnEventsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-get-turn-events-test-')
        self.addCleanup(temporary.cleanup)
        self.__path = os.path.join(temporary.name, 'events.jsonl')

    def __write(self, events):
        with open(self.__path, 'w', encoding='utf-8', newline='\n') as file:
            file.writelines(json.dumps(event, ensure_ascii=False) + '\n' for event in events)

    def test_splits_turns_at_requests_but_not_at_queued_messages(self):
        self.__write([
            _assistant('before'),
            _user('first'),
            _tool_start('view'),
            _user('second'),
            _user('interrupt', queued=True),
            {'type': 'hook.start'},
        ])
        self.assertEqual(_utility['get_turns'](self.__path), [
            [('assistant.message', {'content': 'before'})],
            [('user.message', {'content': 'first'}), ('tool.execution_start', {'toolName': 'view'})],
            [
                ('user.message', {'content': 'second'}),
                ('user.message', {'content': 'interrupt', 'delivery': 'queued'}),
                ('hook.start', {}),
            ],
        ])

    def test_always_returns_at_least_one_turn(self):
        for events, expected in (
            ([], [[]]),
            ([_assistant('only')], [[('assistant.message', {'content': 'only'})]]),
            ([_user('request')], [[], [('user.message', {'content': 'request'})]]),
        ):
            with self.subTest(events=events):
                self.__write(events)
                self.assertEqual(_utility['get_turns'](self.__path), expected)

    def test_rejects_non_object_data(self):
        self.__write([_user('request'), {'type': 'assistant.message', 'data': []}])
        with self.assertRaises(ValueError) as context:
            _utility['get_turns'](self.__path)
        self.assertEqual(str(context.exception), 'Event data must be a JSON object.')

    def test_latest_turn_and_already_requested(self):
        # 差し戻し済みとみなすのは、最新の依頼の中にある、接頭辞で始まるqueuedのuser.messageだけ。
        for name, events, expected in (
            ('requested', [_user('request'), _user(_REASON_PREFIX + ': fix', queued=True)], True),
            ('other hook', [_user('request'), _user('Other reason', queued=True)], False),
            ('earlier request', [_user('old'), _user(_REASON_PREFIX, queued=True), _user('new')], False),
            ('not queued', [_user(_REASON_PREFIX + ' typed by the user')], False),
            ('no request', [], False),
        ):
            with self.subTest(name=name):
                self.__write(events)
                turn, already_requested = _utility['get_latest_turn'](self.__path, _REASON_PREFIX)
                self.assertEqual(turn, _utility['get_turns'](self.__path)[-1])
                self.assertIs(already_requested, expected)

    def test_get_request(self):
        for name, events, expected in (
            ('request', [('user.message', {'content': 'do it'}), ('assistant.message', {'content': 'done'})], 'do it'),
            ('not a string', [('user.message', {'content': None})], None),
            ('before the first request', [('assistant.message', {'content': 'hello'})], None),
            ('empty', [], None),
        ):
            with self.subTest(name=name):
                self.assertEqual(_utility['get_request'](events), expected)

    def test_get_last_assistant_message_skips_blank_and_non_string_content(self):
        events = [
            ('assistant.message', {'content': 'first'}),
            ('assistant.message', {'content': 'last'}),
            ('assistant.message', {'content': ' \n'}),
            ('assistant.message', {'content': None}),
            ('user.message', {'content': 'queued', 'delivery': 'queued'}),
        ]
        self.assertEqual(_utility['get_last_assistant_message'](events), 'last')
        self.assertIsNone(_utility['get_last_assistant_message']([('user.message', {'content': 'request'})]))


    def test_tool_calls_in_completion_order(self):
        events = [
            ('tool.execution_start', {'toolCallId': 'a', 'toolName': 'functions.bash', 'arguments': '{"command":"x"}'}),
            ('tool.execution_start', {'toolCallId': 'b', 'toolName': 'edit', 'arguments': {'path': 'p'}}),
            ('tool.execution_start', {'toolCallId': 'c', 'toolName': 'view'}),
            ('tool.execution_start', {'toolCallId': 1, 'toolName': 'view'}),
            ('tool.execution_complete', {'toolCallId': 'b', 'success': True}),
            ('tool.execution_complete', {'toolCallId': 'unknown', 'success': True}),
            ('tool.execution_complete', {'toolCallId': 'a', 'success': False}),
        ]
        # 完了していない呼び出しは最後に加え、toolCallIdが文字列でない開始は対応づけられないため除く。
        self.assertEqual(_utility['get_tool_calls'](events), [
            ('edit', {'path': 'p'}, {'toolCallId': 'b', 'success': True}),
            ('bash', '{"command":"x"}', {'toolCallId': 'a', 'success': False}),
            ('view', None, None),
        ])

    def test_shell_exit_code(self):
        for completion, expected in (
            ({'shellExecution': {'exitCode': 0}}, 0),
            ({'shellExecution': {'exitCode': 2}}, 2),
            ({'shellExecution': {'exitCode': True}}, None),
            ({'shellExecution': {'exitCode': '0'}}, None),
            ({'shellExecution': None}, None),
            ({}, None),
            (None, None),
        ):
            with self.subTest(completion=completion):
                self.assertEqual(_utility['get_shell_exit_code'](completion), expected)


if __name__ == '__main__':
    _run_tests()
