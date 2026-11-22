import io
import json
import os
import re
import shlex
import sys
import tempfile
import time
import uuid
from typing import cast

# Codexの公開hook入力をCopilot SDK形式に変換する。対応範囲は .codex/README.md を参照。
_runner_directory = os.path.dirname(os.path.abspath(__file__))
_utilities_directory = os.path.join(os.path.dirname(_runner_directory), 'utilities')
sys.path.insert(0, os.path.join(_utilities_directory, 'collect-json-string-values'))
from collect_json_string_values import collect_json_string_values as _collect_json_string_values

sys.path.insert(0, _runner_directory)
import adapted_hook_runtime as _runtime
_EVENT_NAMES = {
    'SessionStart': 'sessionStart',
    'UserPromptSubmit': 'userPromptSubmitted',
    'SubagentStart': 'userPromptSubmitted',
    'PreToolUse': 'preToolUse',
    'PostToolUse': 'postToolUse',
    'Stop': 'agentStop',
}
_SHELL_NAMES = {'Bash', 'exec_command', 'shell_command', 'shell'}
_EXIT_CODE = re.compile(r'^Process exited with code (-?\d+)\s*$', re.MULTILINE)


def _convert_tool(name, arguments):
    name = name.removeprefix('functions.')
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            # custom_tool_callのapply_patchはJSONで包まれていない。
            arguments = {'command': arguments}
    if name in _SHELL_NAMES:
        arguments = dict(arguments) if isinstance(arguments, dict) else {}
        command = arguments.get('command', arguments.get('cmd'))
        if isinstance(command, list) and all(isinstance(part, str) for part in command):
            command = command[-1] if len(command) >= 3 and command[-2] in ('-c', '-lc') else shlex.join(command)
        arguments['command'] = command
        return 'bash', arguments
    return name, arguments


def _convert_hook_input(payload, timestamp_ms):
    event = payload.get('hook_event_name')
    if event not in _EVENT_NAMES:
        raise ValueError(f'unsupported hook_event_name: {event!r}')
    for key in ('session_id', 'cwd'):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f'{key} must be a non-empty string.')
    # サブエージェントのhookは親のsession_idを持つため、状態をagent_idごとに分離する。
    session = payload['session_id']
    agent = payload.get('agent_id')
    if agent:
        session = str(uuid.uuid5(uuid.NAMESPACE_URL, f'codex:{session}:agent:{agent}'))
    else:
        try:
            session = str(uuid.UUID(session))
        except ValueError:
            session = str(uuid.uuid5(uuid.NAMESPACE_URL, f'codex:{session}'))
    converted = {'sessionId': session, 'timestamp': timestamp_ms, 'cwd': payload['cwd']}
    if agent:
        converted['agentId'] = agent
    if event == 'SessionStart':
        converted['source'] = payload.get('source')
    elif event in ('UserPromptSubmit', 'SubagentStart'):
        converted['prompt'] = payload.get('prompt', '') if event == 'UserPromptSubmit' else ''
    elif event in ('PreToolUse', 'PostToolUse'):
        name = payload.get('tool_name')
        if not isinstance(name, str) or not name:
            raise ValueError('tool_name must be a non-empty string.')
        converted['toolName'], converted['toolArgs'] = _convert_tool(name, payload.get('tool_input'))
        if event == 'PostToolUse':
            converted['toolResult'] = {
                'resultType': 'success',
                'textResultForLlm': '\n'.join(_collect_json_string_values(payload.get('tool_response'))),
            }
    return _EVENT_NAMES[event], converted


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(block['text'] for block in content if isinstance(block, dict)
                     and isinstance(block.get('text'), str))


def _shell_exit(output):
    if isinstance(output, str):
        try:
            structured = json.loads(output)
        except ValueError:
            match = _EXIT_CODE.search(output)
            return int(match.group(1)) if match else None
        return _shell_exit(structured)
    if isinstance(output, dict):
        metadata = output.get('metadata', output)
        if isinstance(metadata, dict) and type(metadata.get('exit_code')) is int:
            return metadata['exit_code']
    return None


def _convert_transcript(path, last_message, timestamp_ms):
    # 公開ソースのrollout JSONLに限定する。指示・要約をユーザー依頼として補完しない。
    events = []
    calls = {}
    completed = set()
    recognized = False

    def add(kind, data, timestamp):
        events.append({'type': kind, 'data': data, 'timestamp': timestamp})

    def start(call_id, name, arguments, timestamp):
        if not isinstance(call_id, str) or not call_id or call_id in calls:
            return
        calls[call_id] = name
        add('tool.execution_start', {
            'toolCallId': call_id, 'toolName': name, 'arguments': arguments,
        }, timestamp)

    def complete(call_id, exit_code, timestamp):
        if call_id not in calls or call_id in completed:
            return
        completed.add(call_id)
        data = {'toolCallId': call_id, 'success': True}
        if calls[call_id] == 'bash' and type(exit_code) is int:
            data['shellExecution'] = {'exitCode': exit_code}
        add('tool.execution_complete', data, timestamp)

    with open(path, encoding='utf-8') as file:
        for line in file:
            try:
                entry = json.loads(line)
            except ValueError:
                # 追記途中の末尾以外の破損は報告する。
                if line.endswith('\n'):
                    raise
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get('payload'), dict):
                continue
            kind, item, timestamp = entry.get('type'), entry['payload'], entry.get('timestamp')
            recognized |= kind in ('session_meta', 'event_msg', 'response_item')
            if kind == 'event_msg':
                item_type = item.get('type')
                if item_type in ('user_message', 'agent_message'):
                    content = item.get('message')
                    if isinstance(content, str) and content.strip():
                        add('user.message' if item_type == 'user_message' else 'assistant.message',
                            {'content': content}, timestamp)
                elif item_type == 'item_completed' and isinstance(item.get('item'), dict):
                    item = item['item']
                    item_type = item.get('type')
                    if item_type in ('UserMessage', 'AgentMessage'):
                        content = _text(item.get('content'))
                        if content.strip():
                            add('user.message' if item_type == 'UserMessage' else 'assistant.message',
                                {'content': content}, timestamp)
                    elif item_type == 'HookPrompt':
                        for fragment in item.get('fragments', []):
                            if not isinstance(fragment, dict) or not isinstance(fragment.get('text'), str):
                                continue
                            for reason in fragment['text'].split(_runtime.BLOCK_REASON_SEPARATOR):
                                add('hook.end', {'hookType': 'agentStop', 'output': {
                                    'decision': 'block', 'reason': reason,
                                }}, timestamp)
                                add('user.message', {'content': reason, 'delivery': 'queued'}, timestamp)
                    elif item_type == 'CommandExecution':
                        call_id, command = item.get('id'), item.get('command')
                        # コードモード内の呼び出しにはresponse_itemがない場合がある。
                        # JavaScript本文を解析せず、ホストが記録した実行コマンドを復元する。
                        # write_stdinの通信とユーザー自身のシェル実行は新しい呼び出しにしない。
                        if (item.get('source') in ('agent', 'unified_exec_startup')
                                and isinstance(command, list) and command
                                and all(isinstance(part, str) for part in command)):
                            name, arguments = _convert_tool('shell', {'command': command})
                            start(call_id, name, arguments, timestamp)
                        if type(item.get('exit_code')) is int:
                            complete(call_id, item['exit_code'], timestamp)
            elif kind == 'response_item':
                item_type = item.get('type')
                if item_type in ('function_call', 'custom_tool_call'):
                    name, call_id = item.get('name'), item.get('call_id')
                    if not isinstance(name, str) or not isinstance(call_id, str):
                        continue
                    name, arguments = _convert_tool(name, item.get('arguments', item.get('input')))
                    start(call_id, name, arguments, timestamp)
                elif item_type in ('function_call_output', 'custom_tool_call_output'):
                    call_id = item.get('call_id')
                    exit_code = _shell_exit(item.get('output'))
                    # 起動中・拒否・不明のシェル出力を成功や終了コード0と推定しない。
                    if calls.get(call_id) != 'bash' or exit_code is not None:
                        complete(call_id, exit_code, timestamp)
    if not recognized:
        raise ValueError('unsupported or empty Codex transcript format.')
    return _runtime.complete_transcript_events(events, last_message, timestamp_ms)


def main():
    # 型定義上の `sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    timestamp_ms = time.time_ns() // 1_000_000
    try:
        payload = json.loads(sys.stdin.buffer.read().decode('utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('hook input must be a JSON object.')
        event, hook_input = _convert_hook_input(payload, timestamp_ms)
        entries = _runtime.load_hook_entries(event)
        host_event = payload['hook_event_name']
        with tempfile.TemporaryDirectory(prefix='ghc-codex-hook-') as temporary:
            if event == 'agentStop':
                path = payload.get('transcript_path')
                if not isinstance(path, str) or not path:
                    raise ValueError('Stop requires a readable transcript path.')
                events = _convert_transcript(os.path.expanduser(path), payload.get('last_assistant_message'), timestamp_ms)
                hook_input['transcriptPath'] = _runtime.write_transcript_events(temporary, events)
            outputs, failed = _runtime.run_hook_entries(entries, hook_input)
        # 形式が異なる旧履歴でも差し戻しを繰り返さないよう、続行後のStopでは再ブロックしない。
        if event == 'agentStop' and payload.get('stop_hook_active') is True:
            outputs = [{key: value for key, value in output.items() if key not in ('decision', 'reason')}
                       for output in outputs]
        merged = _runtime.merge_hook_outputs(host_event, outputs)
        if merged:
            sys.stdout.buffer.write(json.dumps(merged, ensure_ascii=False).encode('utf-8') + b'\n')
            return 0
        return 1 if failed else 0
    except (OSError, ValueError, KeyError) as ex:
        print(f'Failed to run Codex hook: {ex}', file=sys.stderr)
        # Codexのexit 2は明示的なブロックなので、アダプター異常には使わない。
        return 1


if __name__ == '__main__':
    sys.exit(main())
