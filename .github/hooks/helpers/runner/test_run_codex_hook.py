import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_helpers_directory = os.path.dirname(_script_directory)
_hooks_directory = os.path.dirname(_helpers_directory)
_repository_root = os.path.dirname(os.path.dirname(_hooks_directory))
sys.path[:0] = [
    os.path.join(_hooks_directory, 'tests', 'helpers'),
    _script_directory,
]
from hook_test_output import run_tests as _run_tests
import run_codex_hook as _adapter
_FAKE_HOOK = '''import json, os, sys
data = json.loads(sys.stdin.buffer.read().decode('utf-8'))
if 'transcriptPath' in data:
    with open(data['transcriptPath'], encoding='utf-8') as stream:
        data['transcript'] = [json.loads(line) for line in stream]
with open(os.path.join(os.path.dirname(__file__), 'input.json'), 'w', encoding='utf-8', newline='\\n') as stream:
    json.dump(data, stream)
print(json.dumps(OUTPUT))
sys.exit(EXIT_CODE)
'''


class RunCodexHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-codex-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.join(os.path.realpath(temporary.name), 'repo with spaces')
        self.__hooks = os.path.join(self.__root, '.github', 'hooks')
        self.__runner = os.path.join(self.__hooks, 'helpers', 'runner')
        os.makedirs(self.__runner)
        for name in ('run_hook.py', 'adapted_hook_runtime.py', 'run_codex_hook.py'):
            shutil.copy(os.path.join(_script_directory, name), self.__runner)
        for name in ('get_state_path', 'hook_activity_state', 'collect_json_string_values'):
            target = os.path.join(self.__hooks, 'helpers', 'utilities', name)
            os.makedirs(target)
            shutil.copy(os.path.join(_helpers_directory, 'utilities', name, name + '.py'), target)
        self.__env = dict(os.environ, TMPDIR=temporary.name, TEMP=temporary.name, TMP=temporary.name)
        self.__session = str(uuid.uuid4())

    def __hook(self, event, name='test-hook', output=None, matcher=None, exit_code=0, repo=False):
        category = '-repo-events' if repo else 'events'
        directory = os.path.join(self.__hooks, 'helpers', category, event, name)
        os.makedirs(directory)
        script_name = name + '.py'
        with open(os.path.join(directory, script_name), 'w', encoding='utf-8', newline='\n') as file:
            file.write('OUTPUT = ' + repr(output or {}) + '\nEXIT_CODE = ' + str(exit_code) + '\n' + _FAKE_HOOK)
        path = os.path.join(self.__hooks, ('-repo-' if repo else '') + event + '.json')
        config = {'hooks': {event: []}}
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as file:
                config = json.load(file)
        entry = {'bash': f'python3 .github/hooks/helpers/runner/run_hook.py .github/hooks/helpers/{category}/{event}/{name}/{script_name}'}
        if matcher:
            entry['matcher'] = matcher
        config['hooks'][event].append(entry)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            json.dump(config, file)
        return os.path.join(directory, 'input.json')

    def __run(self, event, **values):
        payload = dict(session_id=self.__session, cwd=self.__root, hook_event_name=event, **values)
        return subprocess.run([sys.executable, os.path.join(self.__runner, 'run_codex_hook.py')],
                              input=json.dumps(payload).encode('utf-8'), capture_output=True,
                              cwd=os.path.dirname(self.__root), env=self.__env, timeout=30, check=False)

    def __read(self, path):
        with open(path, encoding='utf-8') as file:
            return json.load(file)

    def __transcript(self, *records):
        path = os.path.join(self.__root, 'rollout.jsonl')
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            for kind, payload in records:
                file.write(json.dumps({'type': kind, 'payload': payload}) + '\n')
        return path

    def test_prompt_context_and_session_identity(self):
        path = self.__hook('userPromptSubmitted', output={'additionalContext': '共通指示'})
        result = self.__run('UserPromptSubmit', prompt='依頼')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput'], {
            'hookEventName': 'UserPromptSubmit', 'additionalContext': '共通指示',
        })
        self.assertEqual(self.__read(path)['prompt'], '依頼')
        self.assertEqual(self.__read(path)['sessionId'], self.__session)
        self.__run('SubagentStart', agent_id='child-a')
        first = self.__read(path)
        self.__run('SubagentStart', agent_id='child-b')
        self.assertNotEqual(first['sessionId'], self.__read(path)['sessionId'])
        self.assertEqual((first['agentId'], first['prompt']), ('child-a', ''))
        self.assertNotEqual(first['sessionId'], self.__session)

    def test_patch_matching_and_denial_order(self):
        self.__hook('preToolUse', 'common', {'permissionDecision': 'deny', 'permissionDecisionReason': 'common'}, 'apply_patch')
        path = self.__hook('preToolUse', 'repo', {'permissionDecision': 'deny', 'permissionDecisionReason': 'repo'}, 'apply_patch', repo=True)
        skipped = self.__hook('preToolUse', 'shell', matcher='bash')
        patch = '*** Begin Patch\n*** Add File: a.py\n+text\n*** End Patch'
        result = self.__run('PreToolUse', tool_name='apply_patch', tool_input={'command': patch})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason'], 'repo\n\ncommon')
        self.assertEqual(self.__read(path)['toolArgs'], {'command': patch})
        self.assertFalse(os.path.exists(skipped))

    def test_post_shell_output_and_unknown_tools(self):
        path = self.__hook('postToolUse')
        result = self.__run('PostToolUse', tool_name='Bash', tool_input={'command': 'pytest'},
                            tool_response={'stdout': '1 failed', 'stderr': '詳細', 'exit_code': 1})
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.__read(path)
        self.assertEqual(data['toolName'], 'bash')
        self.assertEqual(data['toolResult'], {'resultType': 'success', 'textResultForLlm': '1 failed\n詳細'})
        self.__run('PostToolUse', tool_name='mcp__fs__read', tool_input={'file': 'a'}, tool_response='content')
        self.assertEqual(self.__read(path)['toolName'], 'mcp__fs__read')

    def test_stop_transcript_and_loop_guard(self):
        path = self.__hook('agentStop', output={'decision': 'block', 'reason': '再確認', 'additionalContext': 'ignored'})
        transcript = self.__transcript(
            ('event_msg', {'type': 'user_message', 'message': '依頼'}),
            ('response_item', {'type': 'function_call', 'name': 'exec_command', 'arguments': '{"cmd":"pytest"}', 'call_id': 't'}),
            ('response_item', {'type': 'function_call_output', 'call_id': 't', 'output': 'Process exited with code 1\n1 failed'}),
        )
        result = self.__run('Stop', transcript_path=transcript, last_assistant_message='応答', stop_hook_active=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'decision': 'block', 'reason': '再確認'})
        data = self.__read(path)
        self.assertFalse(os.path.exists(data['transcriptPath']))
        events = data['transcript']
        self.assertEqual(events[0]['data']['content'], '依頼')
        self.assertEqual(events[1]['data']['arguments']['command'], 'pytest')
        self.assertEqual(events[2]['data']['shellExecution'], {'exitCode': 1})
        self.assertEqual(events[-2]['data']['content'], '応答')
        self.assertEqual(events[-1]['data']['input']['timestamp'], data['timestamp'])
        result = self.__run('Stop', transcript_path=transcript, stop_hook_active=True)
        self.assertEqual((result.returncode, result.stdout), (0, b''))

    def test_subagent_stop_is_not_supported(self):
        config = self.__read(os.path.join(_repository_root, '.codex', 'hooks.json'))['hooks']
        self.assertNotIn('SubagentStop', config)
        path = self.__hook('agentStop', output={'decision': 'block', 'reason': '再確認'})
        result = self.__run('SubagentStop', agent_id='child')
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b"unsupported hook_event_name: 'SubagentStop'", result.stderr)
        self.assertFalse(os.path.exists(path))

    def test_paginated_history_and_incomplete_shell(self):
        transcript = self.__transcript(
            ('response_item', {'type': 'message', 'role': 'user', 'content': [{'text': 'AGENTS instructions'}]}),
            ('event_msg', {'type': 'item_completed', 'item': {'type': 'UserMessage', 'content': [{'type': 'text', 'text': '依頼'}]}}),
            ('response_item', {'type': 'custom_tool_call', 'name': 'apply_patch', 'input': '*** Begin Patch\n*** End Patch', 'call_id': 'p'}),
            ('response_item', {'type': 'function_call', 'name': 'exec_command', 'arguments': '{"cmd":"pytest"}', 'call_id': 't'}),
            ('response_item', {'type': 'function_call_output', 'call_id': 't', 'output': 'Process running with session ID 12'}),
            ('event_msg', {'type': 'item_completed', 'item': {'type': 'CommandExecution', 'id': 't', 'exit_code': 0}}),
            ('event_msg', {'type': 'item_completed', 'item': {'type': 'HookPrompt', 'fragments': [{'text': 'A\n\n---\n\nB'}]}}),
            ('event_msg', {'type': 'item_completed', 'item': {'type': 'AgentMessage', 'content': [{'type': 'Text', 'text': '応答'}]}}),
        )
        events = _adapter._convert_transcript(transcript, '応答', 123)
        self.assertEqual([e['data']['content'] for e in events if e['type'] == 'user.message'], ['依頼', 'A', 'B'])
        self.assertEqual(sum(e['type'] == 'assistant.message' for e in events), 1)
        complete = [e['data'] for e in events if e['type'] == 'tool.execution_complete']
        self.assertEqual(complete, [{'toolCallId': 't', 'success': True, 'shellExecution': {'exitCode': 0}}])
        self.assertEqual(events[1]['data']['arguments'], {'command': '*** Begin Patch\n*** End Patch'})

    def test_missing_or_unknown_transcript_reports_failure(self):
        for path in (None, 'missing', self.__transcript(('alien', {}))):
            with self.subTest(path=path):
                result = self.__run('Stop', transcript_path=path)
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'Failed to run Codex hook', result.stderr)

    def test_code_mode_shell_completion_without_response_item(self):
        path = self.__hook('agentStop')
        command = 'python3 .github/hooks/tests/test_all_hooks.py'
        item = {'type': 'CommandExecution', 'id': 'exec-nested',
                'command': ['/bin/zsh', '-lc', command], 'source': 'unified_exec_startup',
                'status': 'completed', 'exit_code': 1}
        transcript = self.__transcript(
            ('response_item', {'type': 'custom_tool_call', 'name': 'functions.exec',
                               'input': 'await tools.exec_command({cmd: command})', 'call_id': 'outer'}),
            ('event_msg', {'type': 'item_completed', 'item': item}),
            ('event_msg', {'type': 'item_completed', 'item': item}),
            ('response_item', {'type': 'custom_tool_call_output', 'call_id': 'outer', 'output': 'done'}),
        )
        result = self.__run('Stop', transcript_path=transcript)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.__read(path)['transcript']
        nested = [e for e in events if e['data'].get('toolCallId') == 'exec-nested']
        self.assertEqual([e['type'] for e in nested], ['tool.execution_start', 'tool.execution_complete'])
        self.assertEqual(nested[0]['data']['toolName'], 'bash')
        self.assertEqual(nested[0]['data']['arguments'], {'command': command})
        self.assertEqual(nested[1]['data']['shellExecution'], {'exitCode': 1})

    def test_completed_items_do_not_duplicate_direct_calls(self):
        for item_first in (False, True):
            with self.subTest(item_first=item_first):
                call = ('response_item', {'type': 'function_call', 'name': 'functions.exec_command',
                                         'arguments': '{"cmd":"pytest"}', 'call_id': 't'})
                item = ('event_msg', {'type': 'item_completed', 'item': {
                    'type': 'CommandExecution', 'id': 't', 'command': ['sh', '-c', 'pytest'],
                    'source': 'agent', 'exit_code': 0,
                }})
                records = (item, call) if item_first else (call, item)
                transcript = self.__transcript(*records, ('response_item', {
                    'type': 'function_call_output', 'call_id': 't', 'output': '{"exit_code":0}',
                }))
                events = _adapter._convert_transcript(transcript, None, 123)
                self.assertEqual([e['type'] for e in events], [
                    'tool.execution_start', 'tool.execution_complete', 'hook.start',
                ])

    def test_stop_test_requirement_observes_nested_test_exit_code(self):
        sys.path.insert(0, os.path.join(_helpers_directory, 'events', 'agentStop', 'require_hook_tests'))
        import require_hook_tests as requirement
        for exit_code, expected in ((0, []), (1, ['failed with exit code 1']), (None, ['not run'])):
            with self.subTest(exit_code=exit_code):
                transcript = self.__transcript(
                    ('event_msg', {'type': 'user_message', 'message': 'hookを修正'}),
                    ('response_item', {'type': 'custom_tool_call', 'name': 'apply_patch', 'call_id': 'edit',
                                       'input': '*** Begin Patch\n*** Update File: .github/hooks/helpers/runner/run_codex_hook.py\n@@\n-a\n+b\n*** End Patch'}),
                    ('response_item', {'type': 'custom_tool_call_output', 'call_id': 'edit', 'output': 'Success'}),
                    ('event_msg', {'type': 'item_completed', 'item': {
                        'type': 'CommandExecution', 'id': 'nested-test', 'source': 'unified_exec_startup',
                        'command': ['sh', '-c', 'python3 .github/hooks/tests/test_all_hooks.py'],
                        'exit_code': exit_code,
                    }}),
                )
                events = _adapter._convert_transcript(transcript, None, 123)
                with open(transcript, 'w', encoding='utf-8', newline='\n') as file:
                    for event in events:
                        file.write(json.dumps(event) + '\n')
                turn, _ = requirement._get_turn_events(transcript)
                unverified, _ = requirement._get_unverified(turn, _repository_root)
                self.assertEqual([status for _, status in unverified], expected)

    def test_shell_items_do_not_invent_success_or_execution(self):
        for source, command, exit_code, started, completed in (
            ('unified_exec_startup', ['sh', '-c', 'pytest'], None, True, False),
            ('unified_exec_startup', ['sh', '-c', 'pytest'], True, True, False),
            ('unified_exec_startup', ['python3', 'test.py'], 0, True, True),
            ('unified_exec_interaction', ['sh', '-c', 'pytest'], 0, False, False),
            ('user_shell', ['sh', '-c', 'pytest'], 0, False, False),
            ('unified_exec_startup', [], 0, False, False),
            ('unified_exec_startup', 'pytest', 0, False, False),
        ):
            with self.subTest(source=source, command=command, exit_code=exit_code):
                transcript = self.__transcript(('event_msg', {'type': 'item_completed', 'item': {
                    'type': 'CommandExecution', 'id': 'nested', 'source': source,
                    'command': command, 'exit_code': exit_code,
                }}))
                events = _adapter._convert_transcript(transcript, None, 123)
                self.assertEqual(sum(e['type'] == 'tool.execution_start' for e in events), int(started))
                self.assertEqual(sum(e['type'] == 'tool.execution_complete' for e in events), int(completed))
                if completed:
                    self.assertEqual(events[0]['data']['arguments']['command'], 'python3 test.py')

    def test_nested_shell_hook_payload_uses_normal_tool_hooks(self):
        # コードモードのPre/Postは外側のJavaScriptではなく個々のツール入力を受け取る。
        pre = self.__hook('preToolUse', output={'permissionDecision': 'deny',
                                             'permissionDecisionReason': 'blocked'}, matcher='bash')
        post = self.__hook('postToolUse', output={'additionalContext': 'checked'}, matcher='bash')
        for name in ('Bash', 'functions.Bash', 'exec_command', 'functions.exec_command'):
            with self.subTest(name=name):
                arguments = {'command': 'pytest'} if name.endswith('Bash') else {'cmd': 'pytest'}
                result = self.__run('PreToolUse', tool_name=name, tool_input=arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
                self.assertEqual(self.__read(pre)['toolArgs']['command'], 'pytest')
                result = self.__run('PostToolUse', tool_name=name, tool_input=arguments,
                                    tool_response={'output': 'failed', 'exit_code': 1})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['additionalContext'], 'checked')
                self.assertEqual(self.__read(post)['toolResult']['textResultForLlm'], 'failed')

    def test_invalid_input_and_hook_failures_do_not_block(self):
        result = self.__run('PostToolUseFailure')
        self.assertEqual(result.returncode, 1)
        result = self.__run('PreToolUse', tool_name=42)
        self.assertEqual(result.returncode, 1)
        self.__hook('preToolUse', exit_code=2)
        result = self.__run('PreToolUse', tool_name='Bash', tool_input={'command': 'ls'})
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.__hook('preToolUse', 'deny', {'permissionDecision': 'deny', 'permissionDecisionReason': 'deny'})
        result = self.__run('PreToolUse', tool_name='Bash', tool_input={'command': 'ls'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_configuration_launches_from_subdirectory(self):
        config = self.__read(os.path.join(_repository_root, '.codex', 'hooks.json'))['hooks']
        self.assertEqual(set(config), set(_adapter._EVENT_NAMES))
        handlers = [group[0]['hooks'][0] for group in config.values()]
        self.assertTrue(all(handler == handlers[0] for handler in handlers))
        self.assertEqual(handlers[0]['type'], 'command')
        path = self.__hook('sessionStart', output={'additionalContext': 'started'})
        subprocess.run(['git', 'init', self.__root], capture_output=True, check=True, timeout=30)
        nested = os.path.join(self.__root, 'nested')
        os.makedirs(nested)
        payload = {'session_id': self.__session, 'cwd': nested, 'hook_event_name': 'SessionStart'}
        command = handlers[0]['commandWindows' if os.name == 'nt' else 'command']
        if os.name == 'nt':
            # args配列ではlist2cmdlineが内部の引用符をエスケープしてしまう。
            # Codexのraw_argと同じく、コマンド本文は変更せず外側だけ引用する。
            shell = os.environ.get('COMSPEC', 'cmd.exe')
            arguments = f'"{shell}" /c "{command}"'
        else:
            arguments = ['sh', '-c', command]
        result = subprocess.run(arguments, cwd=nested, env=self.__env,
                                input=json.dumps(payload).encode('utf-8'), capture_output=True, timeout=30, check=False)
        diagnostics = f'stdout={result.stdout!r}, stderr={result.stderr!r}'
        self.assertEqual(result.returncode, 0, diagnostics)
        self.assertTrue(os.path.isfile(path), diagnostics)
        self.assertEqual(self.__read(path)['cwd'], nested)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['additionalContext'], 'started')


if __name__ == '__main__':
    _run_tests()
