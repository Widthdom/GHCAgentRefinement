import json
import os
import shutil
import subprocess
import sys
import tempfile

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'block-commands.py')
definitions_path = os.path.join(script_directory, 'blocked-command-patterns.json')


class HookResult:
    def __init__(self, exit_code, output, error):
        self.exit_code = exit_code
        self.output = output
        self.error = error

    def __repr__(self):
        return json.dumps({'ExitCode': self.exit_code, 'Output': self.output, 'Error': self.error}, ensure_ascii=False)


def invoke_hook_input(input_json, entry_point='py', search_path=None):
    environment = os.environ.copy()
    if entry_point == 'cmd':
        arguments = [os.environ['ComSpec'], '/d', '/s', '/c', f'""{os.path.join(script_directory, "block-commands.cmd")}""']
        environment['PATH'] = python_shim_path
    elif entry_point == 'sh':
        arguments = [bash_path, '--noprofile', '--norc', os.path.join(script_directory, 'block-commands.sh').replace('\\', '/')]
        environment['PATH'] = python3_shim_path
    else:
        arguments = [sys.executable, hook_path, '--definitions-path', definitions_path]
    if search_path is not None:
        environment['PATH'] = search_path

    completed = subprocess.run(
        # cmd.exeの引数は独自の引用規則で解釈されるため、文字列のまま渡す。
        ' '.join(arguments) if entry_point == 'cmd' else arguments,
        input=input_json.encode('utf-8'),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    return HookResult(
        completed.returncode,
        completed.stdout.decode('utf-8'),
        completed.stderr.decode('utf-8'),
    )


def invoke_hook(command):
    payload = {
        'toolName': 'powershell',
        'toolArgs': {'command': command},
    }

    result = invoke_hook_input(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    if result.exit_code != 0 or result.error:
        raise AssertionError(f"Hook failed for command '{command}': exit {result.exit_code}, {result.error}")

    return json.loads(result.output)


def create_python_shims(directory):
    # WindowsとUnix系ラッパーが呼び出すPythonを、このテストを実行しているPythonに差し替える。
    shutil.copy2(sys.executable, os.path.join(directory, 'python.exe'))
    with open(os.path.join(directory, 'python3.cmd'), 'w', encoding='utf-8', newline='\r\n') as file:
        file.write(f'@"{sys.executable}" %*\n')
    shell_path = os.path.join(directory, 'python3')
    with open(shell_path, 'w', encoding='utf-8', newline='\n') as file:
        file.write(f'#!/bin/sh\nexec "{sys.executable.replace(chr(92), "/")}" "$@"\n')
    os.chmod(shell_path, 0o755)


test_cases = [
    {'Command': 'git status', 'Expected': None},
    {'Command': 'git rebase main', 'Expected': 'deny'},
    {'Command': 'git -C repo rebase --interactive HEAD~2', 'Expected': 'deny'},
    {'Command': 'Write-Host start; git rebase main', 'Expected': 'deny'},
    {'Command': 'git push origin main', 'Expected': None},
    {'Command': 'git push --force origin main', 'Expected': 'deny'},
    {'Command': 'git push origin main --force-with-lease', 'Expected': 'deny'},
    {'Command': 'git -C repo push -f origin main', 'Expected': 'deny'},
    {'Command': 'Write-Host start; git push --force-if-includes origin main', 'Expected': 'deny'},
    {'Command': 'git reset', 'Expected': 'deny'},
    {'Command': 'git reset --hard HEAD~1', 'Expected': 'deny'},
    {'Command': 'git -C repo reset --soft HEAD^', 'Expected': 'deny'},
    {'Command': 'Write-Host start; git reset HEAD file.txt', 'Expected': 'deny'},
    {'Command': 'git commit -m "normal commit"', 'Expected': None},
    {'Command': 'git commit --no-edit', 'Expected': None},
    {'Command': 'git commit --amend', 'Expected': 'deny'},
    {'Command': 'git commit --amend --no-edit', 'Expected': 'deny'},
    {'Command': 'git commit --no-edit --amend', 'Expected': 'deny'},
    {'Command': 'git commit -m "updated message" --amend', 'Expected': 'deny'},
    {'Command': 'git -C "repo path" commit --amend', 'Expected': 'deny'},
    {'Command': 'git.exe -c user.name=test commit --amend', 'Expected': 'deny'},
    {'Command': 'Write-Host start; git commit --amend', 'Expected': 'deny'},
    {'Command': 'git commit --amend; Write-Host done', 'Expected': 'deny'},
    {'Command': 'git commit --amend-invalid', 'Expected': None},
    {'Command': 'git commit -m "normal"; Write-Host --amend', 'Expected': None},
]

payload_cases = [
    {'Name': 'SDK read', 'Json': '{"toolName":"view","toolArgs":{"path":"README.md"}}'},
    {'Name': 'SDK search', 'Json': '{"toolName":"rg","toolArgs":{"pattern":"git reset"}}'},
    {'Name': 'SDK edit string', 'Json': '{"toolName":"apply_patch","toolArgs":"git reset"}'},
    {'Name': 'non-command without args', 'Json': '{"toolName":"view"}'},
    {'Name': 'anchored tool name', 'Json': '{"toolName":"not_bash","toolArgs":{"command":"git reset"}}'},
    {'Name': 'raw command', 'Json': '{"toolName":"bash","toolArgs":"git reset"}', 'Expected': 'deny'},
    {'Name': 'prefixed bash', 'Json': '{"toolName":"functions.bash","toolArgs":{"command":"git reset"}}', 'Expected': 'deny'},
    {'Name': 'prefixed PowerShell', 'Json': '{"toolName":"functions.powershell","toolArgs":{"command":"git reset"}}', 'Expected': 'deny'},
    {'Name': 'Japanese command', 'Json': '{"toolName":"bash","toolArgs":{"command":"echo 日本語; git reset"}}', 'Expected': 'deny'},
    {'Name': 'non-command batch', 'Json': '{"toolCalls":[{"name":"view","args":{"path":"README.md"}},{"name":"apply_patch","args":"git reset"}]}'},
    {'Name': 'safe mixed batch', 'Json': '{"toolCalls":[{"name":"view"},{"name":"bash","args":{"command":"git status"}},{"name":"powershell","args":"git push origin main"}]}'},
    {'Name': 'later denied command', 'Json': '{"toolCalls":[{"name":"view"},{"name":"bash","args":{"command":"git status"}},{"name":"powershell","args":{"command":"git reset"}}]}', 'Expected': 'deny'},
    {'Name': 'denied command before non-command', 'Json': '{"toolCalls":[{"name":"bash","args":{"command":"git reset"}},{"name":"view"}]}', 'Expected': 'deny'},
    {'Name': 'missing command', 'Json': '{"toolName":"bash","toolArgs":{}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'missing args', 'Json': '{"toolName":"powershell"}', 'Error': 'requires a non-empty command string'},
    {'Name': 'null args', 'Json': '{"toolName":"bash","toolArgs":null}', 'Error': 'requires a non-empty command string'},
    {'Name': 'numeric command', 'Json': '{"toolName":"bash","toolArgs":{"command":42}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'array command', 'Json': '{"toolName":"bash","toolArgs":{"command":["git status"]}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'array args', 'Json': '{"toolName":"bash","toolArgs":[{"command":"git reset"}]}', 'Error': 'requires a non-empty command string'},
    {'Name': 'empty command', 'Json': '{"toolName":"bash","toolArgs":{"command":""}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'blank raw command', 'Json': '{"toolName":"bash","toolArgs":" \\t\\r\\n"}', 'Error': 'requires a non-empty command string'},
    {'Name': 'invalid later batch command', 'Json': '{"toolCalls":[{"name":"bash","args":{"command":"git status"}},{"name":"powershell","args":{}}]}', 'Error': 'requires a non-empty command string'},
    {'Name': 'missing tool name', 'Json': '{"toolArgs":{"command":"git reset"}}', 'Error': 'requires a tool name'},
    {'Name': 'empty tool name', 'Json': '{"toolName":"","toolArgs":{}}', 'Error': 'requires a tool name'},
    {'Name': 'numeric tool name', 'Json': '{"toolName":42,"toolArgs":{}}', 'Error': 'requires a tool name'},
    {'Name': 'array tool name', 'Json': '{"toolName":["bash"],"toolArgs":{"command":"git status"}}', 'Error': 'requires a tool name'},
    {'Name': 'missing batch name', 'Json': '{"toolCalls":[{"args":{"command":"git reset"}}]}', 'Error': 'requires a tool name'},
    {'Name': 'null batch entry', 'Json': '{"toolCalls":[null]}', 'Error': 'requires each tool call to be a JSON object'},
    {'Name': 'string batch entry', 'Json': '{"toolCalls":["bash"]}', 'Error': 'requires each tool call to be a JSON object'},
    {'Name': 'array batch entry', 'Json': '{"toolCalls":[[{"name":"bash","args":{"command":"git status"}}]]}', 'Error': 'requires each tool call to be a JSON object'},
    {'Name': 'object batch', 'Json': '{"toolCalls":{"name":"bash","args":{"command":"git reset"}}}', 'Error': 'must be a non-empty array'},
    {'Name': 'null batch', 'Json': '{"toolCalls":null}', 'Error': 'must be a non-empty array'},
    {'Name': 'empty batch', 'Json': '{"toolCalls":[]}', 'Error': 'must be a non-empty array'},
    {'Name': 'null input', 'Json': 'null', 'Error': 'must be a JSON object'},
    {'Name': 'array input', 'Json': '[]', 'Error': 'must be a JSON object'},
    {'Name': 'single-object array input', 'Json': '[{"toolName":"bash","toolArgs":{"command":"git status"}}]', 'Error': 'must be a JSON object'},
    {'Name': 'string input', 'Json': '"git reset"', 'Error': 'must be a JSON object'},
    {'Name': 'invalid JSON', 'Json': '{', 'Error': 'Failed to parse preToolUse hook input'},
    {'Name': 'empty input', 'Json': '', 'Error': 'hook input is empty'},
]

for test_case in test_cases:
    result = invoke_hook(test_case['Command'])
    if result.get('permissionDecision') != test_case['Expected']:
        raise AssertionError(f"Unexpected decision for '{test_case['Command']}': '{result.get('permissionDecision')}'")

for test_case in payload_cases:
    result = invoke_hook_input(test_case['Json'])
    if test_case.get('Error'):
        if result.exit_code != 2 or result.output or test_case['Error'] not in result.error:
            raise AssertionError(f"Unexpected failure for '{test_case['Name']}': {result!r}")
        continue

    if result.exit_code != 0 or result.error:
        raise AssertionError(f"Unexpected error for '{test_case['Name']}': {result!r}")
    decision = json.loads(result.output)
    if test_case.get('Expected'):
        if (decision.get('permissionDecision') != test_case['Expected'] or
                not str(decision.get('permissionDecisionReason') or '').strip()):
            raise AssertionError(f"Unexpected decision for '{test_case['Name']}': {result.output}")
    elif result.output.strip() != '{}':
        raise AssertionError(f"Unexpected skip/allow output for '{test_case['Name']}': {result.output}")

print(f'Passed {len(test_cases) + len(payload_cases)} block-command hook tests.')

entry_points = []
if os.name == 'nt':
    entry_points.append('cmd')
if os.name == 'nt':
    git_command = shutil.which('git')
    if git_command is None:
        raise AssertionError('git was not found.')
    bash_path = os.path.join(os.path.dirname(os.path.dirname(git_command)), 'bin', 'bash.exe')
else:
    bash_path = shutil.which('bash')
if bash_path and os.path.isfile(bash_path):
    entry_points.append('sh')
else:
    print('Skipping sh wrapper tests: a native bash executable was not found.')

with tempfile.TemporaryDirectory() as shim_directory:
    create_python_shims(shim_directory)
    python_shim_path = shim_directory + os.pathsep + os.environ.get('PATH', '')
    python3_shim_path = shim_directory + os.pathsep + os.environ.get('PATH', '')

    for entry_point in entry_points:
        for test_case in (
            {'Json': '{"toolName":"bash","toolArgs":{"command":"git status"}}', 'Expected': None},
            {'Json': '{"toolName":"powershell","toolArgs":{"command":"echo 日本語; git reset"}}', 'Expected': 'deny'},
        ):
            result = invoke_hook_input(test_case['Json'], entry_point=entry_point)
            if result.exit_code != 0 or result.error:
                raise AssertionError(f'Unexpected {entry_point} wrapper execution: {result!r}')
            output = json.loads(result.output)
            if output.get('permissionDecision') != test_case['Expected']:
                raise AssertionError(f'Unexpected {entry_point} wrapper decision: {result!r}')

        result = invoke_hook_input('{', entry_point=entry_point)
        if result.exit_code != 2 or result.output or 'Failed to parse' not in result.error:
            raise AssertionError(f'Unexpected {entry_point} wrapper error forwarding: {result!r}')

        result = invoke_hook_input('{}', entry_point=entry_point, search_path='')
        output = json.loads(result.output)
        if (result.exit_code != 2 or output.get('permissionDecision') != 'deny' or
                not str(output.get('permissionDecisionReason') or '').strip() or
                'Tool execution denied.' not in result.error):
            raise AssertionError(f'Unexpected {entry_point} wrapper missing-python3 result: {result!r}')
        print(f'Passed 4 {entry_point} wrapper tests.')
