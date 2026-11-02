import json
import os
import subprocess
import sys
import tempfile
import unittest


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'detect-tests-passing-without-change.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


HOOK_TIMESTAMP = 1777777777000
REASON_PREFIX = "The tests still pass without this request's implementation changes"
BASELINE_DATE = '2026-01-01T00:00:00Z'
TURN_STARTED_AT = '2026-01-02T00:00:00.000Z'
IN_TURN_DATE = '2026-01-03T00:00:00Z'
SHELL_TOOL = 'powershell' if os.name == 'nt' else 'bash'
IMPLEMENTATION = 'def add(a, b):\n    return a + b\n'
CHANGED_IMPLEMENTATION = IMPLEMENTATION + '\n\ndef sub(a, b):\n    return a - b\n'
TEST = 'from calc import add\n\nassert add(1, 2) == 3\n'
# 実装の変更（subの追加）を検証しない、変更前のコードでも通るテスト。
WEAK_TEST = TEST + 'assert add(2, 2) == 4\n'
# 実装の変更を検証する、変更前のコードでは失敗するテスト。
STRONG_TEST = TEST + 'from calc import sub\n\nassert sub(3, 1) == 2\n'


def run_python_command(script):
    if SHELL_TOOL == 'powershell':
        return f'& "{sys.executable}" {script}'
    return f'"{sys.executable}" {script}'


def user(content, delivery='idle'):
    return {'type': 'user.message', 'timestamp': TURN_STARTED_AT, 'data': {'content': content, 'delivery': delivery}}


def shell(call_id, command, exit_code, tool_name=SHELL_TOOL):
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


class DetectTestsPassingWithoutChangeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-tests-passing-without-change-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.join(temporary.name, 'repo')
        os.makedirs(self.root)
        self.transcript_path = os.path.join(temporary.name, 'events.jsonl')
        self.payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': HOOK_TIMESTAMP,
            'cwd': self.root,
            'transcriptPath': self.transcript_path,
        }
        self.git('init', '--quiet')
        self.write('calc.py', IMPLEMENTATION)
        self.write('test_calc.py', TEST)
        self.commit(BASELINE_DATE)

    def git(self, *arguments, date=BASELINE_DATE):
        environment = {
            **os.environ,
            'GIT_AUTHOR_NAME': 'test', 'GIT_AUTHOR_EMAIL': 'test@example.com', 'GIT_AUTHOR_DATE': date,
            'GIT_COMMITTER_NAME': 'test', 'GIT_COMMITTER_EMAIL': 'test@example.com', 'GIT_COMMITTER_DATE': date,
        }
        return subprocess.run(
            ['git', '-c', 'commit.gpgsign=false', *arguments], cwd=self.root, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=15,
        ).stdout.decode('utf-8')

    def commit(self, date):
        self.git('add', '--all')
        self.git('commit', '--quiet', '-m', 'commit', date=date)

    def write(self, relative, content):
        with open(os.path.join(self.root, relative), 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def change(self, test_content, implementation_content=CHANGED_IMPLEMENTATION):
        # 実装とテストを書き換え、その編集ツールのイベントを返す。
        self.write('calc.py', implementation_content)
        self.write('test_calc.py', test_content)
        return [
            *edit('e1', {'path': os.path.join(self.root, 'calc.py'), 'old_str': 'a', 'new_str': 'b'}),
            *edit('e2', {'path': 'test_calc.py', 'old_str': 'a', 'new_str': 'b'}),
        ]

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
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_block(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith(REASON_PREFIX))
        self.assertIn('- calc.py', output['reason'])
        self.assertNotIn('test_calc.py\n', output['reason'])
        self.assertIn('Add or strengthen a test', output['reason'])
        return output['reason']

    def assert_worktrees_removed(self):
        self.assertEqual(len(self.git('worktree', 'list', '--porcelain').split('worktree ')) - 1, 1)

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        self.assertEqual(configuration['version'], 1)
        entries = configuration['hooks']['agentStop']
        relative_path = (
            '.github/hooks/helpers/events/agentStop/detect-tests-passing-without-change/'
            'detect-tests-passing-without-change.py'
        )
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['timeoutSec'], 180)
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_tests_passing_without_implementation_change(self):
        events = [user('subを追加して'), *self.change(WEAK_TEST), *shell('c1', run_python_command('test_calc.py'), 0)]
        reason = self.assert_block(self.invoke(events))
        self.assertIn(run_python_command('test_calc.py'), reason)
        self.assert_worktrees_removed()

    def test_allows_tests_failing_without_implementation_change(self):
        events = [user('subを追加して'), *self.change(STRONG_TEST), *shell('c1', run_python_command('test_calc.py'), 0)]
        self.assert_silent(self.invoke(events))
        self.assert_worktrees_removed()

    def test_restores_implementation_committed_during_request(self):
        # 依頼中にコミットしても、依頼開始時点のコミットを基準に実装を戻す。
        for test_content, expected_block in ((WEAK_TEST, True), (STRONG_TEST, False)):
            with self.subTest(expected_block=expected_block):
                events = [user('subを追加してコミットして'), *self.change(test_content)]
                self.commit(IN_TURN_DATE)
                result = self.invoke([*events, *shell('c1', run_python_command('test_calc.py'), 0)])
                if expected_block:
                    self.assert_block(result)
                else:
                    self.assert_silent(result)

    def test_restores_new_implementation_files_by_removing_them(self):
        # 依頼で新規作成した実装ファイルは、依頼開始時点に存在しないため取り除いて実行する。
        self.write('extra.py', 'VALUE = 1\n')
        events = [
            user('extraを追加して'),
            *edit('e1', {'path': 'extra.py', 'file_text': 'VALUE = 1\n'}, 'create'),
            *self.change(TEST + 'from extra import VALUE\n\nassert VALUE == 1\n', IMPLEMENTATION),
            *shell('c1', run_python_command('test_calc.py'), 0),
        ]
        self.assert_silent(self.invoke(events))

    def test_prefers_passing_command_naming_edited_test_file(self):
        # 最後のテスト実行が変更したテストファイルを名指ししていなくても、名指しした成功済みの実行を使う。
        events = [
            user('subを追加して'), *self.change(STRONG_TEST),
            *shell('c1', run_python_command('test_calc.py'), 0), *shell('c2', 'echo tests', 0),
        ]
        self.assert_silent(self.invoke(events))

    def test_rewrites_absolute_repository_paths_in_command(self):
        # 絶対パスでリポジトリのファイルを指すコマンドも、複製の側で実行する。
        command = run_python_command(f'"{os.path.join(self.root, "test_calc.py")}"')
        events = [user('subを追加して'), *self.change(STRONG_TEST), *shell('c1', command, 0)]
        self.assert_silent(self.invoke(events))

    @unittest.skipIf(os.name == 'nt', 'Creating symbolic links on Windows requires extra privileges.')
    def test_handles_repository_reached_through_symbolic_link(self):
        # macOSの `/var` → `/private/var` のように、cwdとgitが返すルートの表記が異なっても検査する。
        link = os.path.join(os.path.dirname(self.root), 'link')
        os.symlink(self.root, link)
        payload = {**self.payload, 'cwd': os.path.join(link, 'sub')}
        os.makedirs(os.path.join(self.root, 'sub'))
        command = run_python_command(f'"{os.path.join(link, "test_calc.py")}"')
        for test_content, expected_block in ((WEAK_TEST, True), (STRONG_TEST, False)):
            with self.subTest(expected_block=expected_block):
                self.write('calc.py', CHANGED_IMPLEMENTATION)
                self.write('test_calc.py', test_content)
                events = [
                    user('subを追加して'),
                    *edit('e1', {'path': os.path.join(link, 'calc.py')}),
                    *edit('e2', {'path': os.path.join('..', 'test_calc.py')}),
                    *shell('c1', command, 0),
                ]
                result = self.invoke(events, payload=payload)
                if expected_block:
                    self.assert_block(result)
                else:
                    self.assert_silent(result)

    def test_ignores_unmet_conditions(self):
        weak = run_python_command('test_calc.py')
        cases = {
            'no test file edited': lambda: [
                user('subを追加して'), *self.change(WEAK_TEST)[:2], *shell('c1', weak, 0)],
            'no implementation file edited': lambda: [
                user('テストを足して'), *self.change(WEAK_TEST)[2:], *shell('c1', weak, 0)],
            'only documents edited as implementation': lambda: [
                user('直して'), *edit('e1', {'path': 'README.md'}), *self.change(WEAK_TEST)[2:], *shell('c1', weak, 0)],
            'failed edit': lambda: [
                user('直して'), *edit('e1', {'path': 'calc.py'}, success=False), *self.change(WEAK_TEST)[2:],
                *shell('c1', weak, 0)],
            'no test command': lambda: [
                user('subを追加して'), *self.change(WEAK_TEST), *shell('c1', 'git diff', 0)],
            'last test failed': lambda: [
                user('subを追加して'), *self.change(WEAK_TEST), *shell('c1', weak, 0), *shell('c2', weak, 1)],
            'last test not executed': lambda: [
                user('subを追加して'), *self.change(WEAK_TEST), *shell('c1', weak, 0), *shell('c2', weak, None)],
            'edits before latest request': lambda: [
                user('subを追加して'), *self.change(WEAK_TEST), *shell('c1', weak, 0), user('ありがとう')],
            'already requested': lambda: [
                user('subを追加して'), *self.change(WEAK_TEST), *shell('c1', weak, 0),
                user(f'{REASON_PREFIX}. ...', 'queued'), *shell('c2', weak, 0)],
        }
        for name, build in cases.items():
            with self.subTest(case=name):
                self.git('checkout', '--quiet', '--', '.')
                self.assert_silent(self.invoke(build()))

    def test_queued_message_does_not_start_new_request(self):
        events = [
            user('subを追加して'), *self.change(WEAK_TEST),
            user('別のhookの差し戻し', 'queued'), *shell('c1', run_python_command('test_calc.py'), 0),
        ]
        self.assert_block(self.invoke(events))

    def test_ignores_subagent_stop(self):
        events = [user('subを追加して'), *self.change(WEAK_TEST), *shell('c1', run_python_command('test_calc.py'), 0)]
        self.assert_silent(self.invoke(events, payload={**self.payload, 'agentId': 'sub-agent'}))

    def test_ignores_request_without_earlier_commit(self):
        # 依頼開始より前のコミットがなければ、戻す基準がないため検査しない。
        events = [user('subを追加して'), *self.change(WEAK_TEST), *shell('c1', run_python_command('test_calc.py'), 0)]
        events[0]['timestamp'] = '2025-12-31T00:00:00.000Z'
        self.assert_silent(self.invoke(events))
        del events[0]['timestamp']
        self.assert_silent(self.invoke(events))

    def test_ignores_outside_git_repository(self):
        outside = tempfile.TemporaryDirectory(prefix='ghc-tests-passing-without-change-outside-')
        self.addCleanup(outside.cleanup)
        events = [
            user('subを追加して'), *edit('e1', {'path': 'calc.py'}), *edit('e2', {'path': 'test_calc.py'}),
            *shell('c1', 'pytest', 0),
        ]
        self.assert_silent(self.invoke(events, payload={**self.payload, 'cwd': outside.name}))

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
                self.assertIn(b'Failed to inspect tests passing without change:', result.stderr)


if __name__ == '__main__':
    run_tests()
