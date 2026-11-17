import io
import json
import os
import re
import runpy
import subprocess
import sys
from datetime import datetime, timezone

# アダプター共通のhook定義の読込・順次実行・判定の集約と、変換したトランスクリプトの仕上げ・書き出し。
_runner_directory = os.path.dirname(os.path.abspath(__file__))
_hooks_directory = os.path.dirname(os.path.dirname(_runner_directory))
_repository_root = os.path.dirname(os.path.dirname(_hooks_directory))
_runner_path = os.path.join(_runner_directory, 'run-hook.py')
_RUNNER_COMMAND_PREFIX = 'python3 .github/hooks/helpers/runner/run-hook.py '
# 複数のagentStop hookの差し戻しを1つの `reason` にまとめるときの区切り。
# アダプターはトランスクリプトの変換時に同じ区切りで分け、hookごとの差し戻しに戻す。
BLOCK_REASON_SEPARATOR = '\n\n---\n\n'


def load_hook_entries(event):
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


def merge_hook_outputs(host_event, outputs):
    # Copilot SDK形式の出力（トップレベルの判定・助言）を、ホストの形式の1つの出力にまとめる。出力がなければNone。
    def get_texts(key):
        return [output[key] for output in outputs if isinstance(output.get(key), str) and output[key]]

    merged = {}
    specific = {}
    if host_event == 'PreToolUse':
        denials = [output for output in outputs if output.get('permissionDecision') == 'deny']
        if denials:
            specific['permissionDecision'] = 'deny'
            specific['permissionDecisionReason'] = '\n\n'.join(
                denial['permissionDecisionReason'] for denial in denials
                if isinstance(denial.get('permissionDecisionReason'), str)
            )
    if host_event == 'Stop':
        reasons = [
            output['reason'] for output in outputs
            if output.get('decision') == 'block' and isinstance(output.get('reason'), str)
        ]
        if reasons:
            merged['decision'] = 'block'
            merged['reason'] = BLOCK_REASON_SEPARATOR.join(reasons)
    # StopのadditionalContextは応答を続行させるため、差し戻しだけを渡す。
    elif contexts := get_texts('additionalContext'):
        specific['additionalContext'] = '\n\n'.join(contexts)
    if messages := get_texts('systemMessage'):
        merged['systemMessage'] = '\n\n'.join(messages)
    if specific:
        merged['hookSpecificOutput'] = {'hookEventName': host_event, **specific}
    return merged or None


def complete_transcript_events(events, last_assistant_message, timestamp_ms):
    # 変換したCopilotのイベントのリストの末尾に、agentStop hookが読むイベントを加えて返す。
    # Stopの時点で、最終応答がトランスクリプトにまだ書き込まれていない場合がある。入力の最終応答で補う。
    if isinstance(last_assistant_message, str) and last_assistant_message.strip():
        last_content = next((
            event['data']['content'] for event in reversed(events) if event['type'] == 'assistant.message'
        ), '')
        if last_content.strip() != last_assistant_message.strip():
            events.append({'type': 'assistant.message', 'timestamp': None, 'data': {'content': last_assistant_message}})
    # hookは、自身のhook.startが記録されるまでトランスクリプトの追記を待つ。待たせないよう末尾に加える。
    events.append({
        'type': 'hook.start',
        'timestamp': datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat(),
        'data': {'hookType': 'agentStop', 'input': {'timestamp': timestamp_ms}},
    })
    return events


def write_transcript_events(directory, events):
    # イベントのリストをdirectoryのevents.jsonlに書き、hookの入力の `transcriptPath` にするパスを返す。
    path = os.path.join(directory, 'events.jsonl').replace(os.sep, '/')
    with open(path, 'w', encoding='utf-8', newline='\n') as file:
        for event in events:
            file.write(json.dumps(event, ensure_ascii=False) + '\n')
    return path


def run_hook_entries(entries, hook_input):
    # (出力のリスト, 異常終了があったか) を返す。
    os.chdir(_repository_root)
    run_hook = runpy.run_path(_runner_path)['main']
    hook_input_bytes = json.dumps(hook_input, ensure_ascii=False).encode('utf-8')
    outputs = []
    failed = False
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

    return outputs, failed
