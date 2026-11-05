import json
import os
import subprocess
import sys
import tempfile
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'require-hook-tests.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


_HOOK_TIMESTAMP = 1777777777000
_PYTHON_COMMAND = 'python' if sys.platform == 'win32' else 'python3'


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _tool(call_id, tool_name, arguments, success=True, exit_code=None):
    complete = {'toolCallId': call_id, 'success': success}
    if exit_code is not None:
        complete['shellExecution'] = {'exitCode': exit_code}
    return [
        {'type': 'tool.execution_start', 'data': {'toolCallId': call_id, 'toolName': tool_name, 'arguments': arguments}},
        {'type': 'tool.execution_complete', 'data': complete},
    ]


def _edit(call_id, path, tool_name='edit', success=True):
    return _tool(call_id, tool_name, {'path': path, 'old_str': 'a', 'new_str': 'b'}, success=success)


def _shell(call_id, command, exit_code, tool_name='bash'):
    return _tool(call_id, tool_name, {'command': command}, success=exit_code is not None, exit_code=exit_code)


def _agent_stop_start():
    return {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}


class RequireHookTestsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-require-hook-tests-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__repository = os.path.join(self.__root, 'repository')
        self.__hooks = os.path.join(self.__repository, '.github', 'hooks')
        for event_name, hook_name in (('preToolUse', 'alpha'), ('agentStop', 'beta'), ('sessionStart', 'no-test')):
            directory = os.path.join(self.__hooks, 'helpers', 'events', event_name, hook_name)
            os.makedirs(directory)
            files = [f'{hook_name}.py', 'README.md']
            if hook_name != 'no-test':
                files.append(f'test-{hook_name}.py')
            for filename in files:
                self.__touch(os.path.join(directory, filename))
        for path in (
            ('helpers', 'utilities', 'shared', 'shared.py'),
            ('helpers', 'runner', 'run-hook.py'),
            ('helpers', 'runner', 'test-run-hook.py'),
            ('helpers', 'runner', 'README.md'),
            ('tests', 'test-all-hooks.py'),
            ('tests', 'test-hook-configurations.py'),
            ('tests', 'README.md'),
            ('tests', 'helpers', 'hook_test_output.py'),
            ('tests', 'helpers', 'test-hook-test-output.py'),
            ('preToolUse.json',),
        ):
            self.__touch(os.path.join(self.__hooks, *path))
        self.__transcript_path = os.path.join(self.__root, 'events.jsonl')
        self.__payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': _HOOK_TIMESTAMP,
            'cwd': self.__repository,
            'transcriptPath': self.__transcript_path,
        }

    @staticmethod
    def __touch(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write('a\n')

    def __hook_file(self, event_name, hook_name, filename):
        return f'.github/hooks/helpers/events/{event_name}/{hook_name}/{filename}'

    def __invoke(self, events=None, payload=None, raw=None):
        if events is not None:
            with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
                for event in [*events, _agent_stop_start()]:
                    file.write(json.dumps(event, ensure_ascii=False) + '\n')
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_block(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'decision', 'reason'})
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith('Hook files were changed'))
        for fragment in fragments:
            self.assertIn(fragment, output['reason'])
        return output['reason']

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        entries = configuration['hooks']['agentStop']
        relative_path = '.github/hooks/helpers/events/agentStop/require-hook-tests/require-hook-tests.py'
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_blocks_hook_change_without_passing_test(self):
        alpha = self.__hook_file('preToolUse', 'alpha', 'alpha.py')
        test = self.__hook_file('preToolUse', 'alpha', 'test-alpha.py')
        cases = (
            ([], 'not run'),
            (_shell('s1', f'python3 {test}', 1), 'failed with exit code 1'),
            (_shell('s1', f'python3 {test}', None), 'did not complete'),
            (_shell('s1', 'python3 test-other-alpha.py', 0), 'not run'),
            (_shell('s1', 'git status', 0), 'not run'),
        )
        for runs, status in cases:
            with self.subTest(status=status, runs=runs):
                events = [_user('直して'), *_edit('e1', alpha), *runs]
                self.__assert_block(self.__invoke(events), f'- preToolUse/alpha ({status}): run `{_PYTHON_COMMAND} {test}`')

    def test_repository_specific_hooks(self):
        directory = os.path.join(self.__hooks, 'helpers', '-repo-events', 'preToolUse', 'gamma')
        for filename in ('gamma.py', 'test-gamma.py', 'README.md'):
            self.__touch(os.path.join(directory, filename))
        gamma = '.github/hooks/helpers/-repo-events/preToolUse/gamma/gamma.py'
        test = '.github/hooks/helpers/-repo-events/preToolUse/gamma/test-gamma.py'
        self.__assert_block(
            self.__invoke([_user('直して'), *_edit('e1', gamma)]),
            f'- preToolUse/gamma (not run): run `{_PYTHON_COMMAND} {test}`',
            '.github/hooks/helpers/-repo-events/preToolUse/gamma/README.md',
        )
        for command in (f'python3 {test}', 'python3 .github/hooks/tests/test-all-hooks.py'):
            with self.subTest(command=command):
                self.__assert_silent(self.__invoke([_user('直して'), *_edit('e1', gamma), *_shell('s1', command, 0)]))
        self.__assert_silent(self.__invoke([
            _user('直して'), *_edit('e1', '.github/hooks/-repo-preToolUse.json'),
            *_shell('s1', 'python3 .github/hooks/tests/test-hook-configurations.py', 0),
        ]))

    def test_requires_rerun_after_later_edit(self):
        alpha = self.__hook_file('preToolUse', 'alpha', 'alpha.py')
        test = self.__hook_file('preToolUse', 'alpha', 'test-alpha.py')
        events = [
            _user('直して'), *_edit('e1', alpha), *_shell('s1', f'python3 {test}', 0),
            *_edit('e2', test, tool_name='functions.create'),
        ]
        self.__assert_block(self.__invoke(events), 'preToolUse/alpha (not run)')

    def test_allows_when_tests_passed_after_last_change(self):
        alpha = self.__hook_file('preToolUse', 'alpha', 'alpha.py')
        beta = os.path.join(self.__hooks, 'helpers', 'events', 'agentStop', 'beta', 'beta.py')
        cases = (
            [*_edit('e1', alpha), *_shell('s1', f'python3 {self.__hook_file("preToolUse", "alpha", "test-alpha.py")}', 0)],
            [*_edit('e1', alpha), *_shell('s1', r'python .github\hooks\helpers\events\preToolUse\alpha\test-alpha.py', 0, 'powershell')],
            [*_edit('e1', alpha), *_edit('e2', beta), *_shell('s1', 'python3 .github/hooks/tests/test-all-hooks.py', 0)],
            [*_edit('e1', alpha), *_shell('s1', 'cd .github/hooks/helpers/events/preToolUse/alpha && python3 test-alpha.py -v', 0)],
            [*_edit('e1', '.github/hooks/preToolUse.json'), *_shell('s1', 'python3 .github/hooks/tests/test-hook-configurations.py', 0)],
            [*_edit('e1', '.github/hooks/helpers/utilities/shared/shared.py'), *_shell('s1', 'python3 .github/hooks/tests/test-all-hooks.py', 0)],
            [*_edit('e1', '.github/hooks/helpers/runner/run-hook.py'),
             *_shell('s1', 'python3 .github/hooks/helpers/runner/test-run-hook.py', 0)],
            [*_edit('e1', '.github/hooks/helpers/runner/run-hook.py'), *_shell('s1', 'python3 .github/hooks/tests/test-all-hooks.py', 0)],
            [*_edit('e1', '.github/hooks/tests/helpers/hook_test_output.py'),
             *_shell('s1', 'python3 .github/hooks/tests/helpers/test-hook-test-output.py', 0)],
        )
        for events in cases:
            with self.subTest(events=events):
                self.__assert_silent(self.__invoke([_user('直して'), *events]))

    def test_ignores_files_that_need_no_test(self):
        cases = (
            [*_edit('e1', self.__hook_file('preToolUse', 'alpha', 'README.md'))],
            [*_edit('e1', '.github/hooks/tests/README.md')],
            [*_edit('e1', '.github/README.md')],
            [*_edit('e1', '.github/hooks/helpers/runner/README.md')],
            [*_edit('e1', 'src/app.py')],
            [*_edit('e1', self.__hook_file('preToolUse', 'deleted', 'deleted.py'))],
            [*_edit('e1', self.__hook_file('preToolUse', 'alpha', 'alpha.py'), success=False)],
            [*_tool('v1', 'str_replace_editor', {'command': 'view', 'path': self.__hook_file('preToolUse', 'alpha', 'alpha.py')})],
            [*_tool('v1', 'view', {'path': self.__hook_file('preToolUse', 'alpha', 'alpha.py')})],
        )
        for events in cases:
            with self.subTest(events=events):
                self.__assert_silent(self.__invoke([_user('直して'), *events]))

    def test_only_changes_since_latest_request(self):
        alpha = self.__hook_file('preToolUse', 'alpha', 'alpha.py')
        self.__assert_silent(self.__invoke([_user('前の依頼'), *_edit('e1', alpha), _user('次の依頼')]))
        # hookの差し戻しなどのqueuedなuser.messageは依頼の区切りとしない。
        self.__assert_block(self.__invoke([_user('依頼'), *_edit('e1', alpha), _user('割り込み', delivery='queued')]))

    def test_blocks_only_once_per_request(self):
        alpha = self.__hook_file('preToolUse', 'alpha', 'alpha.py')
        events = [_user('直して'), *_edit('e1', alpha)]
        reason = self.__assert_block(self.__invoke(events))
        self.__assert_silent(self.__invoke([*events, _user(reason, delivery='queued')]))
        # 次の依頼では改めて差し戻す。
        self.__assert_block(self.__invoke([
            *events, _user(reason, delivery='queued'), _user('もう一度'), *_edit('e2', events[1]['data']['arguments']['path']),
        ]))

    def test_lists_requirements_and_readmes(self):
        patch = (
            '*** Begin Patch\n'
            '*** Update File: .github/hooks/helpers/events/preToolUse/alpha/alpha.py\n@@\n-a\n+b\n'
            '*** Update File: .github/hooks/helpers/events/agentStop/beta/test-beta.py\n@@\n-a\n+b\n'
            '*** Add File: .github/hooks/helpers/events/sessionStart/no-test/no-test.py\n+a\n'
            '*** Update File: .github/hooks/helpers/events/sessionStart/no-test/README.md\n@@\n-a\n+b\n'
            '*** End Patch\n'
        )
        events = [
            _user('直して'),
            *_tool('p1', 'apply_patch', patch),
            *_edit('e1', '.github/hooks/helpers/utilities/shared/shared.py'),
            *_edit('e2', '.github/hooks/preToolUse.json'),
            *_edit('e3', '.github/hooks/tests/helpers/hook_test_output.py'),
            # 一括実行は共通出力処理のテストを含まない。
            *_shell('s1', 'python3 .github/hooks/tests/test-all-hooks.py', 1),
        ]
        reason = self.__assert_block(
            self.__invoke(events),
            f'- preToolUse/alpha (failed with exit code 1): run `{_PYTHON_COMMAND} .github/hooks/helpers/events/preToolUse/alpha/test-alpha.py`',
            '- agentStop/beta (failed with exit code 1)',
            '- sessionStart/no-test: the test file `.github/hooks/helpers/events/sessionStart/no-test/test-no-test.py` does not exist.',
            '- shared utilities (helpers/utilities) (failed with exit code 1)',
            '- hook configurations (failed with exit code 1)',
            f'- tests/helpers/test-hook-test-output.py (not run): run `{_PYTHON_COMMAND} .github/hooks/tests/helpers/test-hook-test-output.py`',
        )
        self.assertTrue(reason.endswith(
            '\nAlso check whether these READMEs need updating for the implementation changes: '
            '.github/hooks/helpers/events/preToolUse/alpha/README.md'
        ))
        # テストだけを変えたhookと、READMEも変えたhookは、READMEの確認を促さない。
        self.assertNotIn('beta/README.md', reason)
        self.assertNotIn('no-test/README.md', reason)

    def test_requires_runner_test(self):
        reason = self.__assert_block(
            self.__invoke([_user('直して'), *_edit('e1', '.github/hooks/helpers/runner/run-hook.py')]),
            f'- hook runner (helpers/runner) (not run): run `{_PYTHON_COMMAND} .github/hooks/helpers/runner/test-run-hook.py`',
        )
        self.assertTrue(reason.endswith('.github/hooks/helpers/runner/README.md'))

    def test_skips_subagent(self):
        events = [_user('直して'), *_edit('e1', self.__hook_file('preToolUse', 'alpha', 'alpha.py'))]
        self.__invoke(events)
        self.__assert_silent(self.__invoke(payload={**self.__payload, 'agentId': 'agent-1'}))

    def test_invalid_input_reports_error(self):
        self.__invoke([_user('直して')])
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42']
        for changes in (
            {'cwd': None}, {'cwd': 'relative'}, {'transcriptPath': None}, {'transcriptPath': ' '},
            {'transcriptPath': os.path.join(self.__root, 'missing.jsonl')},
        ):
            invalid.append(json.dumps({**self.__payload, **changes}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertNotEqual(result.stderr, b'')
        with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write('{"type":"user.message","data":[]}\n')
        result = self.__invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'Failed to inspect hook tests:', result.stderr)


if __name__ == '__main__':
    run_tests()
