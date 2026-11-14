import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid


_directory = os.path.dirname(os.path.abspath(__file__))
_hooks = os.path.dirname(os.path.dirname(_directory))
_root = os.path.dirname(os.path.dirname(_hooks))
_run_tests = runpy.run_path(os.path.join(_hooks, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']
_adapter = runpy.run_path(os.path.join(_directory, 'run-codex-hook.py'))
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
        for name in ('run-hook.py', 'adapted-hook-runtime.py', 'run-codex-hook.py'):
            shutil.copy(os.path.join(_directory, name), self.__runner)
        for name in ('get-state-path', 'hook-activity-state', 'collect-json-string-values'):
            target = os.path.join(self.__hooks, 'helpers', 'utilities', name)
            os.makedirs(target)
            shutil.copy(os.path.join(_hooks, 'helpers', 'utilities', name, name + '.py'), target)
        self.__env = dict(os.environ, TMPDIR=temporary.name, TEMP=temporary.name, TMP=temporary.name)
        self.__session = str(uuid.uuid4())

    def __hook(self, event, name='test-hook', output=None, matcher=None, exit_code=0, repo=False):
        category = '-repo-events' if repo else 'events'
        directory = os.path.join(self.__hooks, 'helpers', category, event, name)
        os.makedirs(directory)
        with open(os.path.join(directory, name + '.py'), 'w', encoding='utf-8', newline='\n') as file:
            file.write('OUTPUT = ' + repr(output or {}) + '\nEXIT_CODE = ' + str(exit_code) + '\n' + _FAKE_HOOK)
        path = os.path.join(self.__hooks, ('-repo-' if repo else '') + event + '.json')
        config = {'hooks': {event: []}}
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as file:
                config = json.load(file)
        entry = {'bash': f'python3 .github/hooks/helpers/runner/run-hook.py .github/hooks/helpers/{category}/{event}/{name}/{name}.py'}
        if matcher:
            entry['matcher'] = matcher
        config['hooks'][event].append(entry)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            json.dump(config, file)
        return os.path.join(directory, 'input.json')

    def __run(self, event, **values):
        payload = dict(session_id=self.__session, cwd=self.__root, hook_event_name=event, **values)
        return subprocess.run([sys.executable, os.path.join(self.__runner, 'run-codex-hook.py')],
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
        config = self.__read(os.path.join(_root, '.codex', 'hooks.json'))['hooks']
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
        events = _adapter['_convert_transcript'](transcript, '応答', 123)
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
        config = self.__read(os.path.join(_root, '.codex', 'hooks.json'))['hooks']
        self.assertEqual(set(config), set(_adapter['_EVENT_NAMES']))
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
