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
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests

_RUNNER_FILES = ('run_hook.py', 'run_claude_hook.py', 'run-claude-hook.sh', 'adapted_hook_runtime.py')
_COPIED_UTILITIES = (
    'collect-json-string-values', 'get-state-path', 'hook-activity-state', 'parse-tool-calls', 'read-transcript-events',
)
_LAUNCHER_COMMAND = 'sh "$CLAUDE_PROJECT_DIR/.github/hooks/helpers/runner/run-claude-hook.sh"'
_CLAUDE_EVENTS = (
    'PostToolUse', 'PostToolUseFailure', 'PreToolUse', 'SessionStart', 'Stop', 'SubagentStart', 'UserPromptSubmit',
)

# 受け取った入力と、入力のトランスクリプトの内容を、自身のフォルダーに追記する偽のhook。
# 出力と終了コードは、同じフォルダーの output.json・exit-code.txt があればそれに従う。
_FAKE_HOOK = '''import json
import os
import sys

directory = os.path.dirname(os.path.abspath(__file__))
hook_input = json.loads(sys.stdin.buffer.read().decode('utf-8'))
transcript_path = hook_input.get('transcriptPath')
if transcript_path is not None:
    with open(transcript_path, encoding='utf-8') as file:
        hook_input['transcript'] = [json.loads(line) for line in file]
with open(os.path.join(directory, 'inputs.jsonl'), 'a', encoding='utf-8', newline='\\n') as file:
    file.write(json.dumps(hook_input) + '\\n')
output_path = os.path.join(directory, 'output.json')
if os.path.isfile(output_path):
    with open(output_path, 'rb') as file:
        sys.stdout.buffer.write(file.read())
exit_code_path = os.path.join(directory, 'exit-code.txt')
if os.path.isfile(exit_code_path):
    with open(exit_code_path, encoding='utf-8') as file:
        sys.exit(int(file.read()))
'''


class RunClaudeHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-run-claude-hook-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        # アダプターは自身の位置からリポジトリルートとhook定義を決めるため、同じ構成を一時領域に作る。
        self.__repository = os.path.join(self.__root, 'repository')
        self.__hooks = os.path.join(self.__repository, '.github', 'hooks')
        os.makedirs(os.path.join(self.__hooks, 'helpers', 'runner'))
        for filename in _RUNNER_FILES:
            shutil.copy(os.path.join(_script_directory, filename), os.path.join(self.__hooks, 'helpers', 'runner', filename))
        for utility in _COPIED_UTILITIES:
            os.makedirs(os.path.join(self.__hooks, 'helpers', 'utilities', utility))
            filename = f"{utility.replace('-', '_')}.py"
            shutil.copy(
                os.path.join(_helpers_directory, 'utilities', utility, filename),
                os.path.join(self.__hooks, 'helpers', 'utilities', utility, filename),
            )
        self.__adapter_path = os.path.join(self.__hooks, 'helpers', 'runner', 'run_claude_hook.py')
        self.__temp_directory = os.path.join(self.__root, 'temp')
        os.makedirs(self.__temp_directory)
        self.__session_id = str(uuid.uuid4())

    def __hook_directory(self, event, hook_name, events_name='events'):
        return os.path.join(self.__hooks, 'helpers', events_name, event, hook_name)

    def __add_hook(self, event, hook_name, matcher=None, output=None, exit_code=None, events_name='events'):
        # 偽のhookを作り、イベントのhook定義に加える。
        directory = self.__hook_directory(event, hook_name, events_name)
        os.makedirs(directory)
        script_name = f"{hook_name.replace('-', '_')}.py"
        with open(os.path.join(directory, script_name), 'w', encoding='utf-8', newline='\n') as file:
            file.write(_FAKE_HOOK)
        if output is not None:
            with open(os.path.join(directory, 'output.json'), 'w', encoding='utf-8', newline='\n') as file:
                file.write(output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))
        if exit_code is not None:
            with open(os.path.join(directory, 'exit-code.txt'), 'w', encoding='utf-8', newline='\n') as file:
                file.write(str(exit_code))
        script = f'.github/hooks/helpers/{events_name}/{event}/{hook_name}/{script_name}'
        entry = {
            'type': 'command',
            'bash': f'python3 .github/hooks/helpers/runner/run_hook.py {script}',
            'powershell': f'python .github/hooks/helpers/runner/run_hook.py {script}',
        }
        if matcher is not None:
            entry['matcher'] = matcher
        self.__add_entry(event, entry, '-repo-' if events_name == '-repo-events' else '')

    def __add_entry(self, event, entry, prefix=''):
        path = os.path.join(self.__hooks, f'{prefix}{event}.json')
        configuration = {'version': 1, 'hooks': {event: []}}
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as file:
                configuration = json.load(file)
        configuration['hooks'][event].append(entry)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            json.dump(configuration, file)

    def __inputs(self, event, hook_name, events_name='events'):
        # 偽のhookが受け取った入力のリスト。起動していなければ空。
        path = os.path.join(self.__hook_directory(event, hook_name, events_name), 'inputs.jsonl')
        if not os.path.isfile(path):
            return []
        with open(path, encoding='utf-8') as file:
            return [json.loads(line) for line in file]

    def __run(self, command, stdin):
        # 状態を実際の一時フォルダーに残さないよう、一時フォルダーを差し替える。カレントディレクトリには依存させない。
        environment = dict(os.environ, TMPDIR=self.__temp_directory, TMP=self.__temp_directory, TEMP=self.__temp_directory)
        return subprocess.run(
            command, input=stdin, capture_output=True, env=environment, cwd=self.__root, check=False, timeout=60,
        )

    def __run_adapter(self, event, **values):
        payload = {'session_id': self.__session_id, 'cwd': self.__repository, 'hook_event_name': event, **values}
        result = self.__run([sys.executable, self.__adapter_path], json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        return result, json.loads(result.stdout.decode('utf-8')) if result.stdout else None

    def __write_transcript(self, *entries):
        path = os.path.join(self.__root, 'transcript.jsonl')
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(''.join(json.dumps(entry) + '\n' for entry in entries))
        return path

    def test_converts_tool_call_and_applies_matcher(self):
        self.__add_hook('preToolUse', 'shell-hook', matcher='(?:functions\\.)?(?:bash|powershell)')
        self.__add_hook('preToolUse', 'edit-hook', matcher='(?:functions\\.)?(?:edit|create)')
        self.__add_hook('preToolUse', 'every-hook')
        self.__add_hook('preToolUse', 'repo-hook', matcher='(?:functions\\.)?bash', events_name='-repo-events')
        cases = (
            ('Bash', {'command': 'ls'}, 'bash', ('shell-hook', 'repo-hook')),
            ('Edit', {'file_path': 'a.py', 'old_string': 'a', 'new_string': 'b'}, 'edit', ('edit-hook',)),
            ('Write', {'file_path': 'a.py', 'content': 'a'}, 'create', ('edit-hook',)),
            ('Read', {'file_path': 'a.py'}, 'view', ()),
            ('WebFetch', {'url': 'https://example.com'}, 'web_fetch', ()),
            ('mcp__server__tool', {}, 'mcp__server__tool', ()),
        )
        for index, (tool_name, tool_input, expected_name, expected_hooks) in enumerate(cases, 1):
            with self.subTest(tool_name=tool_name):
                result, output = self.__run_adapter('PreToolUse', tool_name=tool_name, tool_input=tool_input, agent_id='agent-1')
                self.assertEqual((result.returncode, output, result.stderr), (0, None, b''))
                hook_input = self.__inputs('preToolUse', 'every-hook')[-1]
                self.assertIs(type(hook_input.pop('timestamp')), int)
                self.assertEqual(hook_input, {
                    'sessionId': self.__session_id, 'cwd': self.__repository, 'agentId': 'agent-1',
                    'toolName': expected_name, 'toolArgs': tool_input,
                })
                self.assertEqual(len(self.__inputs('preToolUse', 'every-hook')), index)
                ran = tuple(
                    hook_name for hook_name, events_name in (
                        ('shell-hook', 'events'), ('edit-hook', 'events'), ('repo-hook', '-repo-events'),
                    ) if any(run['toolName'] == expected_name for run in self.__inputs('preToolUse', hook_name, events_name))
                )
                self.assertEqual(ran, expected_hooks)

    def test_merges_denials(self):
        self.__add_hook('preToolUse', 'a-hook', output={'permissionDecision': 'deny', 'permissionDecisionReason': '理由1'})
        self.__add_hook('preToolUse', 'b-hook')
        self.__add_hook('preToolUse', 'c-hook', output={'permissionDecision': 'deny', 'permissionDecisionReason': 'reason 2'})
        result, output = self.__run_adapter('PreToolUse', tool_name='Bash', tool_input={'command': 'ls'})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output, {'hookSpecificOutput': {
            'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': '理由1\n\nreason 2',
        }})
        # 拒否するhookがあっても、残りのhookを実行する。
        self.assertEqual(len(self.__inputs('preToolUse', 'c-hook')), 1)

    def test_merges_additional_context_and_system_message(self):
        self.__add_hook('sessionStart', 'a-hook', output={'additionalContext': 'context 1'})
        self.__add_hook('sessionStart', 'b-hook', output={'systemMessage': 'message'})
        self.__add_hook('sessionStart', 'c-hook', output={'additionalContext': 'context 2'})
        self.__add_hook('sessionStart', 'd-hook', output='not json')
        result, output = self.__run_adapter('SessionStart', source='startup')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output, {
            'systemMessage': 'message',
            'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': 'context 1\n\ncontext 2'},
        })
        hook_input = self.__inputs('sessionStart', 'a-hook')[0]
        self.assertEqual(hook_input['source'], 'startup')
        # VS Code Local harnessの判定に使われる項目は渡さない。
        self.assertNotIn('hook_event_name', hook_input)

    def test_converts_prompt_events(self):
        self.__add_hook('userPromptSubmitted', 'prompt-hook', output={'additionalContext': 'context'})
        result, output = self.__run_adapter('UserPromptSubmit', prompt='依頼')
        self.assertEqual(output, {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': 'context'}})
        # サブエージェントの開始は、agentId付きのuserPromptSubmittedとして渡す。
        result, output = self.__run_adapter('SubagentStart', agent_id='agent-1', agent_type='Explore')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output, {'hookSpecificOutput': {'hookEventName': 'SubagentStart', 'additionalContext': 'context'}})
        prompt_input, subagent_input = self.__inputs('userPromptSubmitted', 'prompt-hook')
        self.assertEqual((prompt_input['prompt'], 'agentId' in prompt_input), ('依頼', False))
        self.assertEqual((subagent_input['prompt'], subagent_input['agentId']), ('', 'agent-1'))

    def test_converts_tool_results(self):
        self.__add_hook('postToolUse', 'post-hook', output={'additionalContext': 'context'})
        self.__add_hook('postToolUseFailure', 'failure-hook')
        bash = {'tool_name': 'Bash', 'tool_input': {'command': 'pytest'}}

        result, output = self.__run_adapter(
            'PostToolUse', **bash, tool_response={'stdout': 'out', 'stderr': 'err', 'interrupted': False},
        )
        self.assertEqual(output, {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': 'context'}})
        self.assertEqual(
            self.__inputs('postToolUse', 'post-hook')[-1]['toolResult'],
            {'resultType': 'success', 'textResultForLlm': 'out\nerr'},
        )

        # シェルの非ゼロ終了は、Copilotと同じくpostToolUseに渡す。出力には、実際に発火したイベント名を使う。
        result, output = self.__run_adapter('PostToolUseFailure', **bash, error='Exit code 1\n1 failed', is_interrupt=False)
        self.assertEqual(output, {'hookSpecificOutput': {'hookEventName': 'PostToolUseFailure', 'additionalContext': 'context'}})
        self.assertEqual(
            self.__inputs('postToolUse', 'post-hook')[-1]['toolResult'],
            {'resultType': 'success', 'textResultForLlm': '1 failed'},
        )
        self.assertEqual(self.__inputs('postToolUseFailure', 'failure-hook'), [])

        # 中断と、シェル以外のツールの失敗は、postToolUseFailureに渡す。
        failures = (
            dict(bash, error='Exit code 137\nkilled', is_interrupt=True),
            dict(bash, error='Command timed out', is_interrupt=False),
            {'tool_name': 'Read', 'tool_input': {'file_path': 'a'}, 'error': 'Exit code 1\nmissing', 'is_interrupt': False},
        )
        for index, failure in enumerate(failures, 1):
            with self.subTest(failure=failure):
                result, output = self.__run_adapter('PostToolUseFailure', **failure)
                self.assertEqual((result.returncode, output), (0, None))
                failure_inputs = self.__inputs('postToolUseFailure', 'failure-hook')
                self.assertEqual(len(failure_inputs), index)
                self.assertEqual(failure_inputs[-1]['error'], failure['error'])
                self.assertNotIn('toolResult', failure_inputs[-1])
        self.assertEqual(len(self.__inputs('postToolUse', 'post-hook')), 2)

    def test_stop_converts_transcript_and_merges_blocks(self):
        self.__add_hook('agentStop', 'a-hook', output={'decision': 'block', 'reason': 'A: fix this.'})
        self.__add_hook('agentStop', 'b-hook', output={'additionalContext': 'ignored'})
        self.__add_hook('agentStop', 'c-hook', output={'decision': 'block', 'reason': 'C: fix that.'})
        common = {'isSidechain': False, 'timestamp': '2026-01-01T00:00:00.000Z'}

        def assistant(*blocks):
            return {**common, 'type': 'assistant', 'message': {'role': 'assistant', 'content': list(blocks)}}

        def tool_use(tool_id, name, tool_input):
            return assistant({'type': 'tool_use', 'id': tool_id, 'name': name, 'input': tool_input})

        def tool_result(tool_id, content, is_error=False, **values):
            block = {'type': 'tool_result', 'tool_use_id': tool_id, 'content': content, 'is_error': is_error}
            return {**common, 'type': 'user', 'message': {'role': 'user', 'content': [block]}, **values}

        def user(content, **values):
            return {**common, 'type': 'user', 'message': {'role': 'user', 'content': content}, **values}

        transcript_path = self.__write_transcript(
            user('最初の依頼'),
            assistant({'type': 'text', 'text': '最初の応答'}),
            user('依頼'),
            {**common, 'type': 'attachment', 'attachment': {'type': 'hook_success', 'hookEvent': 'UserPromptSubmit'}},
            assistant({'type': 'thinking', 'thinking': ''}),
            assistant({'type': 'text', 'text': '途中の文'}),
            tool_use('t1', 'Bash', {'command': 'pytest'}),
            tool_result('t1', 'Exit code 1\n1 failed', True),
            tool_use('t2', 'Bash', {'command': 'ls'}),
            tool_result('t2', 'a.py'),
            tool_use('t3', 'Bash', {'command': 'rm -rf /'}),
            tool_result('t3', 'PreToolUse:Bash hook error: denied', True, toolDenialKind='permission-rule'),
            tool_use('t4', 'Bash', {'command': 'sleep 100', 'run_in_background': True}),
            tool_result('t4', 'started'),
            tool_use('t5', 'Edit', {'file_path': 'a.py', 'old_string': 'a', 'new_string': 'b'}),
            tool_result('t5', [{'type': 'text', 'text': 'updated'}]),
            tool_use('t6', 'Read', {'file_path': 'missing.py'}),
            tool_result('t6', 'File does not exist.', True),
            {**common, 'isSidechain': True, 'type': 'user', 'message': {'role': 'user', 'content': 'サブエージェントへの依頼'}},
            assistant({'type': 'text', 'text': '前の最終応答'}),
            user('Stop hook feedback:\nA: fix this.\n\n---\n\nC: fix that.', isMeta=True),
            {**common, 'type': 'attachment', 'attachment': {
                'type': 'hook_blocking_error', 'hookEvent': 'Stop',
                'blockingError': {'blockingError': 'A: fix this.\n\n---\n\nC: fix that.', 'command': 'command hook'},
            }},
            user('圧縮後の要約', isCompactSummary=True),
            tool_use('t7', 'Bash', {'command': 'git status'}),
        )
        with open(transcript_path, 'a', encoding='utf-8', newline='\n') as file:
            file.write('{"type": "assistant", "mess')

        result, output = self.__run_adapter('Stop', transcript_path=transcript_path, last_assistant_message='最終応答')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output, {'decision': 'block', 'reason': 'A: fix this.\n\n---\n\nC: fix that.'})

        hook_input = self.__inputs('agentStop', 'a-hook')[0]
        events = [(event['type'], event['data']) for event in hook_input['transcript']]
        latest_turn = events[next(index for index, event in enumerate(events) if event[1].get('content') == '依頼') - 1:]
        block_a = {'hookType': 'agentStop', 'output': {'decision': 'block', 'reason': 'A: fix this.'}}
        block_c = {'hookType': 'agentStop', 'output': {'decision': 'block', 'reason': 'C: fix that.'}}
        self.assertEqual(latest_turn, [
            ('hook.start', {'hookType': 'userPromptSubmitted'}),
            ('user.message', {'content': '依頼'}),
            ('assistant.message', {'content': '途中の文'}),
            ('hook.start', {'hookType': 'preToolUse'}),
            ('tool.execution_start', {'toolCallId': 't1', 'toolName': 'bash', 'arguments': {'command': 'pytest'}}),
            ('tool.execution_complete', {'toolCallId': 't1', 'success': True, 'shellExecution': {'exitCode': 1}}),
            ('hook.start', {'hookType': 'postToolUse'}),
            ('hook.start', {'hookType': 'preToolUse'}),
            ('tool.execution_start', {'toolCallId': 't2', 'toolName': 'bash', 'arguments': {'command': 'ls'}}),
            ('tool.execution_complete', {'toolCallId': 't2', 'success': True, 'shellExecution': {'exitCode': 0}}),
            ('hook.start', {'hookType': 'postToolUse'}),
            ('hook.start', {'hookType': 'preToolUse'}),
            ('tool.execution_start', {'toolCallId': 't3', 'toolName': 'bash', 'arguments': {'command': 'rm -rf /'}}),
            ('tool.execution_complete', {'toolCallId': 't3', 'success': False}),
            ('hook.start', {'hookType': 'preToolUse'}),
            ('tool.execution_start', {
                'toolCallId': 't4', 'toolName': 'bash', 'arguments': {'command': 'sleep 100', 'run_in_background': True},
            }),
            ('tool.execution_complete', {'toolCallId': 't4', 'success': True}),
            ('hook.start', {'hookType': 'postToolUse'}),
            ('hook.start', {'hookType': 'preToolUse'}),
            ('tool.execution_start', {
                'toolCallId': 't5', 'toolName': 'edit', 'arguments': {'file_path': 'a.py', 'old_string': 'a', 'new_string': 'b'},
            }),
            ('tool.execution_complete', {'toolCallId': 't5', 'success': True}),
            ('hook.start', {'hookType': 'postToolUse'}),
            ('hook.start', {'hookType': 'preToolUse'}),
            ('tool.execution_start', {'toolCallId': 't6', 'toolName': 'view', 'arguments': {'file_path': 'missing.py'}}),
            ('tool.execution_complete', {'toolCallId': 't6', 'success': False}),
            ('hook.start', {'hookType': 'postToolUseFailure'}),
            ('assistant.message', {'content': '前の最終応答'}),
            ('hook.end', block_a),
            ('user.message', {'content': 'A: fix this.', 'delivery': 'queued'}),
            ('hook.end', block_c),
            ('user.message', {'content': 'C: fix that.', 'delivery': 'queued'}),
            ('hook.start', {'hookType': 'preToolUse'}),
            ('tool.execution_start', {'toolCallId': 't7', 'toolName': 'bash', 'arguments': {'command': 'git status'}}),
            ('assistant.message', {'content': '最終応答'}),
            ('hook.start', {'hookType': 'agentStop', 'input': {'timestamp': hook_input['timestamp']}}),
        ])

        # 変換したトランスクリプトを、hookが使うutilityで読める。
        sys.path.insert(0, os.path.join(_helpers_directory, 'utilities', 'read-transcript-events'))
        import read_transcript_events as transcript_events
        converted_path = os.path.join(self.__root, 'converted.jsonl')
        with open(converted_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(''.join(json.dumps(event) + '\n' for event in hook_input['transcript']))
        self.assertEqual(len(transcript_events.get_turn_events(converted_path)), 3)
        for reason_prefix, expected in (('A:', True), ('C:', True), ('B:', False)):
            turn_events, already_requested = transcript_events.get_latest_turn(converted_path, reason_prefix)
            self.assertEqual(already_requested, expected)
        self.assertEqual(transcript_events.get_request(turn_events), '依頼')
        self.assertEqual(transcript_events.get_request_texts(turn_events), ['依頼'])
        self.assertEqual(transcript_events.get_last_assistant_message(turn_events), '最終応答')
        self.assertEqual(
            [
                (tool_name, transcript_events.get_shell_exit_code(completion))
                for tool_name, _, completion in transcript_events.get_tool_calls(turn_events)
            ],
            [('bash', 1), ('bash', 0), ('bash', None), ('bash', None), ('edit', None), ('view', None), ('bash', None)],
        )
        # 自身のhook.startが記録済みなので、待たずに戻る。
        transcript_events.wait_for_agent_stop_event(converted_path, hook_input['timestamp'], timeout_seconds=0)
        # 変換したトランスクリプトは、hookの実行後に残さない。
        self.assertFalse(os.path.exists(hook_input['transcriptPath']))

    def test_stop_does_not_duplicate_recorded_last_message(self):
        self.__add_hook('agentStop', 'a-hook')
        transcript_path = self.__write_transcript(
            {'type': 'user', 'message': {'role': 'user', 'content': '依頼'}},
            {'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': '最終応答\n'}]}},
        )
        result, output = self.__run_adapter('Stop', transcript_path=transcript_path, last_assistant_message='最終応答')
        self.assertEqual((result.returncode, output), (0, None))
        events = [event['type'] for event in self.__inputs('agentStop', 'a-hook')[0]['transcript']]
        self.assertEqual(events, ['hook.start', 'user.message', 'assistant.message', 'hook.start'])

    def test_hook_failure_does_not_block(self):
        self.__add_hook('preToolUse', 'a-hook', exit_code=2)
        self.__add_hook('preToolUse', 'b-hook')
        tool_call = {'tool_name': 'Bash', 'tool_input': {'command': 'ls'}}
        result, output = self.__run_adapter('PreToolUse', **tool_call)
        # Claude Codeでは終了コード2がブロックを意味するため、1で終える。失敗したhookの後のhookも実行する。
        self.assertEqual((result.returncode, output), (1, None))
        self.assertEqual(len(self.__inputs('preToolUse', 'b-hook')), 1)

        # 判定を出力したhookがあれば、他のhookが失敗していても判定を伝える。
        self.__add_hook('preToolUse', 'c-hook', output={'permissionDecision': 'deny', 'permissionDecisionReason': 'reason'}, exit_code=2)
        result, output = self.__run_adapter('PreToolUse', **tool_call)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output['hookSpecificOutput']['permissionDecisionReason'], 'reason')

    def test_runs_command_hook_without_runner(self):
        # hookランナーを通さないhookは、hook定義のコマンドのまま、リポジトリルートで実行する。
        directory = self.__hook_directory('preToolUse', 'direct-hook')
        os.makedirs(directory)
        with open(os.path.join(directory, 'direct_hook.py'), 'w', encoding='utf-8', newline='\n') as file:
            file.write(_FAKE_HOOK)
        with open(os.path.join(directory, 'output.json'), 'w', encoding='utf-8', newline='\n') as file:
            json.dump({'permissionDecision': 'deny', 'permissionDecisionReason': 'direct'}, file)
        script = '.github/hooks/helpers/events/preToolUse/direct-hook/direct_hook.py'
        self.__add_entry('preToolUse', {
            'type': 'command', 'matcher': 'bash', 'timeoutSec': 30,
            'bash': f'"{sys.executable}" {script}', 'powershell': f'& "{sys.executable}" {script}',
        })
        result, output = self.__run_adapter('PreToolUse', tool_name='Bash', tool_input={'command': 'ls'})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output['hookSpecificOutput']['permissionDecisionReason'], 'direct')
        self.assertEqual(self.__inputs('preToolUse', 'direct-hook')[0]['toolName'], 'bash')

    def test_rejects_invalid_input(self):
        self.__add_hook('preToolUse', 'a-hook')
        cases = (
            (b'', b'Failed to prepare Claude Code hook'),
            (b'[]', b'hook input must be a JSON object'),
            (json.dumps({'hook_event_name': 'Notification'}).encode('utf-8'), b"unsupported hook_event_name: 'Notification'"),
            (json.dumps({'session_id': self.__session_id}).encode('utf-8'), b'unsupported hook_event_name: None'),
        )
        for stdin, error in cases:
            with self.subTest(stdin=stdin):
                result = self.__run([sys.executable, self.__adapter_path], stdin)
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(error, result.stderr)
        self.assertEqual(self.__inputs('preToolUse', 'a-hook'), [])

    def test_event_without_definition_does_nothing(self):
        result, output = self.__run_adapter('SessionStart', source='startup')
        self.assertEqual((result.returncode, output, result.stderr), (0, None, b''))

    @unittest.skipIf(os.name == 'nt', 'The launcher needs a POSIX shell.')
    def test_launcher_runs_adapter(self):
        self.__add_hook('sessionStart', 'a-hook', output={'additionalContext': 'context'})
        payload = {'session_id': self.__session_id, 'cwd': self.__repository, 'hook_event_name': 'SessionStart'}
        result = self.__run(
            ['sh', os.path.join(self.__hooks, 'helpers', 'runner', 'run-claude-hook.sh')], json.dumps(payload).encode('utf-8'),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.decode('utf-8'))['hookSpecificOutput']['additionalContext'], 'context')

    def test_claude_settings_register_every_supported_event(self):
        with open(os.path.join(_repository_root, '.claude', 'settings.json'), encoding='utf-8') as file:
            hooks = json.load(file)['hooks']
        expected = [{'hooks': [{'type': 'command', 'command': _LAUNCHER_COMMAND}]}]
        self.assertEqual(hooks, {event: expected for event in _CLAUDE_EVENTS})
        # 設定のイベントは、アダプターが対応するイベントと一致する。
        sys.path.insert(0, _script_directory)
        import run_claude_hook as adapter
        self.assertEqual(sorted(adapter._EVENT_NAMES), list(_CLAUDE_EVENTS))


if __name__ == '__main__':
    _run_tests()
