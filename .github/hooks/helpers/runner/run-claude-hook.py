import io
import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import cast

# Claude Codeのhookとして起動され、Copilot SDK形式のhookをそのまま実行するアダプター。
# Claude Codeの入力をCopilot SDK形式に変換し、hook定義（.github/hooks/*.json）のhookを定義順に実行して、
# 出力をClaude Codeの形式にまとめる。個々のhookはCopilot SDK形式だけを扱い、Claude Codeを意識しない。
# 設定と変換の規則、制約は .claude/README.md を参照。

_runner_directory = os.path.dirname(os.path.abspath(__file__))
_helpers_directory = os.path.dirname(_runner_directory)
_hooks_directory = os.path.dirname(_helpers_directory)
# hooks → .github → リポジトリルート
_repository_root = os.path.dirname(os.path.dirname(_hooks_directory))
_runner_path = os.path.join(_runner_directory, 'run-hook.py')
_utilities_directory = os.path.join(_helpers_directory, 'utilities')
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_SHELL_TOOL_NAMES = runpy.run_path(
    os.path.join(_utilities_directory, 'parse-tool-calls', 'parse-tool-calls.py'),
)['SHELL_TOOL_NAMES']

# hookランナーを通すhookの起動コマンドの先頭。続く部分が実行スクリプトのリポジトリルート相対パス。
_RUNNER_COMMAND_PREFIX = 'python3 .github/hooks/helpers/runner/run-hook.py '
# Claude Codeのイベント名と、対応するCopilot SDKのイベント名。
# サブエージェントへの依頼は、Copilotでは `agentId` 付きのuserPromptSubmittedとして発火する。
_EVENT_NAMES = {
    'SessionStart': 'sessionStart',
    'UserPromptSubmit': 'userPromptSubmitted',
    'SubagentStart': 'userPromptSubmitted',
    'PreToolUse': 'preToolUse',
    'PostToolUse': 'postToolUse',
    'PostToolUseFailure': 'postToolUseFailure',
    'Stop': 'agentStop',
}
# Claude Codeのツール名と、対応するCopilotのツール名。ここにないツールは名前を変えない。
# 引数は変換しない。編集ツールの引数名の違いは edit-tool-changes.py が吸収する。
_TOOL_NAMES = {
    'Bash': 'bash',
    'PowerShell': 'powershell',
    'Edit': 'edit',
    'Write': 'create',
    'Read': 'view',
    'Grep': 'grep',
    'Glob': 'glob',
    'WebFetch': 'web_fetch',
    'Skill': 'skill',
}
# Claude Codeは、シェルの非ゼロ終了を「Exit code <終了コード>」で始まるエラーとして返す。
_EXIT_CODE_PATTERN = re.compile(r'Exit code (-?\d+)(?:\n|$)')
# 複数のagentStop hookの差し戻しを1つの `reason` にまとめるときの区切り。
# トランスクリプトの変換時に同じ区切りで分け、hookごとの差し戻しに戻す。
_BLOCK_REASON_SEPARATOR = '\n\n---\n\n'


def _convert_tool_name(tool_name):
    return _TOOL_NAMES.get(tool_name, tool_name)


def _get_shell_exit(tool_name, error):
    # シェルの非ゼロ終了を表すエラーなら (終了コード, 出力) を返す。それ以外はNone。
    if tool_name not in _SHELL_TOOL_NAMES or not isinstance(error, str):
        return None
    match = _EXIT_CODE_PATTERN.match(error)
    return (int(match.group(1)), error[match.end():]) if match else None


def _convert_hook_input(claude_input, timestamp_ms):
    # Claude Codeの入力から、Copilot SDKのイベント名と入力を返す。対応しないイベントはValueError。
    # transcriptPathは、変換したトランスクリプトの保存先が決まってから呼び出し元が加える。
    claude_event = claude_input.get('hook_event_name')
    if claude_event not in _EVENT_NAMES:
        raise ValueError(f'unsupported hook_event_name: {claude_event!r}')
    event = _EVENT_NAMES[claude_event]
    hook_input = {
        'sessionId': claude_input.get('session_id'),
        'timestamp': timestamp_ms,
        'cwd': claude_input.get('cwd'),
    }
    if 'agent_id' in claude_input:
        hook_input['agentId'] = claude_input['agent_id']
    if claude_event == 'SessionStart':
        hook_input['source'] = claude_input.get('source')
    elif claude_event == 'UserPromptSubmit':
        hook_input['prompt'] = claude_input.get('prompt')
    elif claude_event == 'SubagentStart':
        # Claude CodeのSubagentStartには依頼文がない。
        hook_input['prompt'] = ''
    elif 'tool_name' in claude_input:
        tool_name = claude_input['tool_name']
        if isinstance(tool_name, str):
            tool_name = _convert_tool_name(tool_name)
        hook_input['toolName'] = tool_name
        hook_input['toolArgs'] = claude_input.get('tool_input')
        if claude_event == 'PostToolUse':
            output = '\n'.join(_collect_json_string_values(claude_input.get('tool_response')))
            hook_input['toolResult'] = {'resultType': 'success', 'textResultForLlm': output}
        elif claude_event == 'PostToolUseFailure':
            # Copilotでは、シェルの非ゼロ終了はツールの成功としてpostToolUseに渡される。
            shell_exit = None if claude_input.get('is_interrupt') else _get_shell_exit(tool_name, claude_input.get('error'))
            if shell_exit is not None:
                event = 'postToolUse'
                hook_input['toolResult'] = {'resultType': 'success', 'textResultForLlm': shell_exit[1]}
            else:
                hook_input['error'] = claude_input.get('error')
    return event, hook_input


def _get_text(content):
    # メッセージやツール結果の `content`（文字列、またはブロックのリスト）から文を取り出す。
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(
        block['text'] for block in content
        if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str)
    )


def _convert_transcript(transcript_path, last_assistant_message, timestamp_ms):
    # Claude Codeのトランスクリプトを、read-transcript-events.py が読むCopilotのイベントのリストに変換する。
    events = []
    shell_call_ids = set()

    def add(event_type, data, timestamp):
        events.append({'type': event_type, 'timestamp': timestamp, 'data': data})

    def add_hook_start(event, timestamp):
        # Claude Codeは、出力のないhookの起動を記録しない。起動したはずのイベントを、やり取りから補う。
        add('hook.start', {'hookType': event}, timestamp)

    with open(transcript_path, encoding='utf-8') as file:
        for line in file:
            try:
                entry = json.loads(line)
            except ValueError:
                # 追記中の不完全な行は読み飛ばす。
                continue
            # サブエージェントのやり取り（isSidechain）は親のイベントにしない。
            if not isinstance(entry, dict) or entry.get('isSidechain'):
                continue
            timestamp = entry.get('timestamp')
            entry_type = entry.get('type')
            message = entry.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            if entry_type == 'attachment':
                # Stop hookの差し戻しは、hook_blocking_errorとして `reason` がそのまま記録される。
                attachment = entry.get('attachment')
                if not isinstance(attachment, dict) or attachment.get('type') != 'hook_blocking_error':
                    continue
                blocking_error = attachment.get('blockingError')
                reason = blocking_error.get('blockingError') if isinstance(blocking_error, dict) else None
                if attachment.get('hookEvent') != 'Stop' or not isinstance(reason, str):
                    continue
                for part in reason.split(_BLOCK_REASON_SEPARATOR):
                    add('hook.end', {'hookType': 'agentStop', 'output': {'decision': 'block', 'reason': part}}, timestamp)
                    add('user.message', {'content': part, 'delivery': 'queued'}, timestamp)
            elif entry_type == 'assistant' and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get('type') == 'text' and isinstance(block.get('text'), str) and block['text'].strip():
                        add('assistant.message', {'content': block['text']}, timestamp)
                    elif block.get('type') == 'tool_use' and isinstance(block.get('name'), str):
                        tool_name = _convert_tool_name(block['name'])
                        arguments = block.get('input')
                        # バックグラウンド実行は終了コードが分からないため、シェル実行として扱わない。
                        if tool_name in _SHELL_TOOL_NAMES and not (
                            isinstance(arguments, dict) and arguments.get('run_in_background')
                        ):
                            shell_call_ids.add(block.get('id'))
                        add_hook_start('preToolUse', timestamp)
                        add('tool.execution_start', {
                            'toolCallId': block.get('id'), 'toolName': tool_name, 'arguments': arguments,
                        }, timestamp)
            # isMeta：hookの差し戻しなど、ユーザーが入力していない文。isCompactSummary：圧縮後の要約。
            elif entry_type == 'user' and not entry.get('isMeta') and not entry.get('isCompactSummary'):
                results = [
                    block for block in content if isinstance(block, dict) and block.get('type') == 'tool_result'
                ] if isinstance(content, list) else []
                for block in results:
                    data = {'toolCallId': block.get('tool_use_id'), 'success': block.get('is_error') is not True}
                    if block.get('tool_use_id') in shell_call_ids:
                        if data['success']:
                            data['shellExecution'] = {'exitCode': 0}
                        elif match := _EXIT_CODE_PATTERN.match(_get_text(block.get('content'))):
                            # Copilotでは、シェルの非ゼロ終了はツールの成功として記録される。
                            data['success'] = True
                            data['shellExecution'] = {'exitCode': int(match.group(1))}
                    add('tool.execution_complete', data, timestamp)
                    # hookやユーザーが拒否した呼び出し（toolDenialKind）では、実行後のhookは起動しない。
                    if data['success']:
                        add_hook_start('postToolUse', timestamp)
                    elif 'toolDenialKind' not in entry:
                        add_hook_start('postToolUseFailure', timestamp)
                text = _get_text(content)
                if not results and text.strip():
                    add_hook_start('userPromptSubmitted', timestamp)
                    add('user.message', {'content': text}, timestamp)

    # Stopの時点で、最終応答がトランスクリプトにまだ書き込まれていない場合がある。入力の最終応答で補う。
    if isinstance(last_assistant_message, str) and last_assistant_message.strip():
        last_content = next((
            event['data']['content'] for event in reversed(events) if event['type'] == 'assistant.message'
        ), '')
        if last_content.strip() != last_assistant_message.strip():
            add('assistant.message', {'content': last_assistant_message}, None)
    # hookは、自身のhook.startが記録されるまでトランスクリプトの追記を待つ。待たせないよう末尾に加える。
    add(
        'hook.start', {'hookType': 'agentStop', 'input': {'timestamp': timestamp_ms}},
        datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat(),
    )
    return events


def _load_hook_entries(event):
    # イベントのhook定義の要素を、このリポジトリ専用の定義（-repo-<event-name>.json）、共通の定義の順に返す。
    entries = []
    for filename in (f'-repo-{event}.json', f'{event}.json'):
        path = os.path.join(_hooks_directory, filename)
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as file:
                entries.extend(json.load(file)['hooks'][event])
    return entries


def _run_in_process(run_hook, script, hook_input_bytes):
    # hookランナーを、標準入出力を差し替えてこのプロセス内で実行する。hookごとにPythonを起動しないため。
    # 戻り値は (終了コード, 標準出力のバイト列)。標準エラーはランナーがそのまま中継する。
    stdout_buffer = io.BytesIO()
    # 差し替えたストリームは、参照がなくなると下のBytesIOごと閉じられるため、値を取り出すまで保持する。
    streams = (
        io.TextIOWrapper(io.BytesIO(hook_input_bytes), encoding='utf-8'),
        io.TextIOWrapper(stdout_buffer, encoding='utf-8', newline='\n'),
    )
    saved = (sys.stdin, sys.stdout, sys.argv)
    sys.stdin, sys.stdout = streams
    sys.argv = [_runner_path, script]
    try:
        exit_code = run_hook()
        streams[1].flush()
        return exit_code, stdout_buffer.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.argv = saved


def _run_command(entry, hook_input_bytes):
    # hookランナーを通さないhook（.sh・.cmd）を、hook定義のコマンドのまま子プロセスで実行する。
    if os.name == 'nt':
        command = ['powershell', '-NoProfile', '-NonInteractive', '-Command', entry['powershell']]
    else:
        command = ['bash', '-c', entry['bash']]
    try:
        result = subprocess.run(
            command, input=hook_input_bytes, stdout=subprocess.PIPE, cwd=_repository_root,
            timeout=entry.get('timeoutSec'), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as ex:
        print(f'Failed to run hook command: {ex}', file=sys.stderr)
        return 1, b''
    return result.returncode, result.stdout


def _parse_hook_output(output_bytes):
    # hookの標準出力をJSONオブジェクトとして返す。出力がない、またはJSONオブジェクトでなければNone。
    try:
        output = json.loads(output_bytes.decode('utf-8'))
    except ValueError:
        return None
    return output if isinstance(output, dict) else None


def _merge_hook_outputs(claude_event, outputs):
    # Copilot SDK形式の出力（トップレベルの判定・助言）を、Claude Codeの形式の1つの出力にまとめる。出力がなければNone。
    def get_texts(key):
        return [output[key] for output in outputs if isinstance(output.get(key), str) and output[key]]

    merged = {}
    specific = {}
    if claude_event == 'PreToolUse':
        denials = [output for output in outputs if output.get('permissionDecision') == 'deny']
        if denials:
            specific['permissionDecision'] = 'deny'
            specific['permissionDecisionReason'] = '\n\n'.join(
                denial['permissionDecisionReason'] for denial in denials
                if isinstance(denial.get('permissionDecisionReason'), str)
            )
    if claude_event == 'Stop':
        reasons = [
            output['reason'] for output in outputs
            if output.get('decision') == 'block' and isinstance(output.get('reason'), str)
        ]
        if reasons:
            merged['decision'] = 'block'
            merged['reason'] = _BLOCK_REASON_SEPARATOR.join(reasons)
    # StopのadditionalContextは応答を続行させるため、差し戻しだけを渡す。
    elif contexts := get_texts('additionalContext'):
        specific['additionalContext'] = '\n\n'.join(contexts)
    if messages := get_texts('systemMessage'):
        merged['systemMessage'] = '\n\n'.join(messages)
    if specific:
        merged['hookSpecificOutput'] = {'hookEventName': claude_event, **specific}
    return merged or None


def main():
    # 型定義上の `sys.stderr` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    timestamp_ms = time.time_ns() // 1_000_000
    try:
        claude_input = json.loads(sys.stdin.buffer.read().decode('utf-8'))
        if not isinstance(claude_input, dict):
            raise ValueError('hook input must be a JSON object.')
        event, hook_input = _convert_hook_input(claude_input, timestamp_ms)
        entries = _load_hook_entries(event)
    except (OSError, ValueError, KeyError) as ex:
        print(f'Failed to prepare Claude Code hook: {ex}', file=sys.stderr)
        return 1
    claude_event = claude_input['hook_event_name']

    # hook定義のコマンドはリポジトリルートからの相対パスで書かれている。
    os.chdir(_repository_root)
    run_hook = runpy.run_path(_runner_path)['main']
    outputs = []
    failed = False
    with tempfile.TemporaryDirectory(prefix='ghc-claude-hook-') as temporary_directory:
        if claude_event == 'Stop':
            try:
                events = _convert_transcript(
                    os.path.expanduser(claude_input.get('transcript_path') or ''),
                    claude_input.get('last_assistant_message'), timestamp_ms,
                )
                hook_input['transcriptPath'] = os.path.join(temporary_directory, 'events.jsonl')
                with open(hook_input['transcriptPath'], 'w', encoding='utf-8', newline='\n') as file:
                    for transcript_event in events:
                        file.write(json.dumps(transcript_event, ensure_ascii=False) + '\n')
            except OSError as ex:
                print(f'Failed to convert Claude Code transcript: {ex}', file=sys.stderr)
                return 1
        hook_input_bytes = json.dumps(hook_input, ensure_ascii=False).encode('utf-8')
        tool_name = hook_input.get('toolName')
        for entry in entries:
            if 'matcher' in entry and isinstance(tool_name, str) and not re.fullmatch(entry['matcher'], tool_name):
                continue
            if entry['bash'].startswith(_RUNNER_COMMAND_PREFIX):
                exit_code, output_bytes = _run_in_process(
                    run_hook, entry['bash'].removeprefix(_RUNNER_COMMAND_PREFIX), hook_input_bytes,
                )
            else:
                exit_code, output_bytes = _run_command(entry, hook_input_bytes)
            failed = failed or exit_code != 0
            output = _parse_hook_output(output_bytes)
            if output is not None:
                outputs.append(output)

    merged = _merge_hook_outputs(claude_event, outputs)
    if merged is not None:
        sys.stdout.buffer.write(json.dumps(merged, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n')
        sys.stdout.buffer.flush()
        return 0
    # Claude Codeでは終了コード2がブロックを意味する。hookの異常終了でツールや応答を止めないよう、1で終える。
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
