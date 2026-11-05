import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-commands.py')
_definitions_path = os.path.join(_script_directory, 'blocked-command-definitions.json')


class _HookResult:
    def __init__(self, exit_code, output, error):
        self.exit_code = exit_code
        self.output = output
        self.error = error

    def __repr__(self):
        return json.dumps({'ExitCode': self.exit_code, 'Output': self.output, 'Error': self.error}, ensure_ascii=False)


def _assert_error_denial(result, expected_error):
    output = json.loads(result.output)
    if (result.exit_code != 2 or expected_error not in result.error or
            output != {
                'permissionDecision': 'deny',
                'permissionDecisionReason': result.error.replace('\r\n', '\n').rstrip('\n'),
            }):
        raise AssertionError(f'Unexpected error denial: {result!r}')


def _create_python_shims(directory):
    # Unix系ラッパーが呼び出すpython3を、このテストを実行しているPythonに差し替える。
    with open(os.path.join(directory, 'python3.cmd'), 'w', encoding='utf-8', newline='\r\n') as file:
        file.write(f'@"{sys.executable}" %*\n')
    shell_path = os.path.join(directory, 'python3')
    with open(shell_path, 'w', encoding='utf-8', newline='\n') as file:
        file.write(f'#!/bin/sh\nexec "{sys.executable.replace(chr(92), "/")}" "$@"\n')
    os.chmod(shell_path, 0o755)


_test_cases = [
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

_payload_cases = [
    {'Name': 'SDK read', 'Json': '{"toolName":"view","toolArgs":{"path":"README.md"}}'},
    {'Name': 'SDK search', 'Json': '{"toolName":"rg","toolArgs":{"pattern":"git reset"}}'},
    {'Name': 'SDK edit string', 'Json': '{"toolName":"apply_patch","toolArgs":"git reset"}'},
    {'Name': 'non-command without args', 'Json': '{"toolName":"view"}'},
    {'Name': 'anchored tool name', 'Json': '{"toolName":"not_bash","toolArgs":{"command":"git reset"}}'},
    {'Name': 'raw command', 'Json': '{"toolName":"bash","toolArgs":"git reset"}', 'Expected': 'deny'},
    {'Name': 'prefixed bash', 'Json': '{"toolName":"functions.bash","toolArgs":{"command":"git reset"}}', 'Expected': 'deny'},
    {'Name': 'prefixed PowerShell', 'Json': '{"toolName":"functions.powershell","toolArgs":{"command":"git reset"}}', 'Expected': 'deny'},
    {'Name': 'Japanese command', 'Json': '{"toolName":"bash","toolArgs":{"command":"echo 日本語; git reset"}}', 'Expected': 'deny'},
    {'Name': 'uppercase tool name', 'Json': '{"toolName":"FUNCTIONS.PowerShell","toolArgs":"git reset"}', 'Expected': 'deny'},
    {'Name': 'case-sensitive tool key', 'Json': '{"ToolName":"bash","toolArgs":"git reset"}', 'Error': 'requires a tool name'},
    {'Name': 'case-sensitive command key', 'Json': '{"toolName":"bash","toolArgs":{"Command":"git reset"}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'batch input', 'Json': '{"toolCalls":[{"name":"bash","args":"git reset"}]}', 'Error': 'requires a tool name'},
    {'Name': 'boolean command', 'Json': '{"toolName":"bash","toolArgs":{"command":true}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'missing command', 'Json': '{"toolName":"bash","toolArgs":{}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'missing args', 'Json': '{"toolName":"powershell"}', 'Error': 'requires a non-empty command string'},
    {'Name': 'null args', 'Json': '{"toolName":"bash","toolArgs":null}', 'Error': 'requires a non-empty command string'},
    {'Name': 'numeric command', 'Json': '{"toolName":"bash","toolArgs":{"command":42}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'array command', 'Json': '{"toolName":"bash","toolArgs":{"command":["git status"]}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'array args', 'Json': '{"toolName":"bash","toolArgs":[{"command":"git reset"}]}', 'Error': 'requires a non-empty command string'},
    {'Name': 'empty command', 'Json': '{"toolName":"bash","toolArgs":{"command":""}}', 'Error': 'requires a non-empty command string'},
    {'Name': 'blank raw command', 'Json': '{"toolName":"bash","toolArgs":" \\t\\r\\n"}', 'Error': 'requires a non-empty command string'},
    {'Name': 'missing tool name', 'Json': '{"toolArgs":{"command":"git reset"}}', 'Error': 'requires a tool name'},
    {'Name': 'empty tool name', 'Json': '{"toolName":"","toolArgs":{}}', 'Error': 'requires a tool name'},
    {'Name': 'numeric tool name', 'Json': '{"toolName":42,"toolArgs":{}}', 'Error': 'requires a tool name'},
    {'Name': 'array tool name', 'Json': '{"toolName":["bash"],"toolArgs":{"command":"git status"}}', 'Error': 'requires a tool name'},
    {'Name': 'null input', 'Json': 'null', 'Error': 'must be a JSON object'},
    {'Name': 'array input', 'Json': '[]', 'Error': 'must be a JSON object'},
    {'Name': 'single-object array input', 'Json': '[{"toolName":"bash","toolArgs":{"command":"git status"}}]', 'Error': 'must be a JSON object'},
    {'Name': 'string input', 'Json': '"git reset"', 'Error': 'must be a JSON object'},
    {'Name': 'invalid JSON', 'Json': '{', 'Error': 'Failed to parse preToolUse hook input'},
    {'Name': 'unescaped control character', 'Json': '{"toolName":"bash","toolArgs":"git\treset"}', 'Error': 'Failed to parse preToolUse hook input'},
    {'Name': 'invalid UTF-8', 'Json': b'{"toolName":"bash","toolArgs":"git reset \xff"}', 'Error': 'Failed to parse preToolUse hook input'},
    {'Name': 'empty input', 'Json': '', 'Error': 'hook input is empty'},
]

_valid_definition = {'name': 'test rule', 'pattern': 'blocked', 'reason': '拒否理由'}
_definition_cases = [
    {'Name': 'empty array', 'Text': '[]', 'Expected': None},
    {'Name': 'valid definition', 'Text': json.dumps([_valid_definition]), 'Expected': 'deny'},
    {'Name': 'UTF-8 BOM', 'Text': json.dumps([_valid_definition]), 'Encoding': 'utf-8-sig', 'Expected': 'deny'},
    {'Name': 'empty file', 'Text': '', 'Error': 'Failed to parse'},
    {'Name': 'blank file', 'Text': ' \r\n\t', 'Error': 'Failed to parse'},
    {'Name': 'null definitions', 'Text': 'null', 'Error': 'must be a JSON array'},
    {'Name': 'single object', 'Text': json.dumps(_valid_definition), 'Error': 'must be a JSON array'},
    {'Name': 'invalid JSON', 'Text': '[', 'Error': 'Failed to parse'},
    {'Name': 'UTF-16', 'Text': json.dumps([_valid_definition]), 'Encoding': 'utf-16', 'Error': 'Failed to parse'},
    {'Name': 'UTF-32', 'Text': json.dumps([_valid_definition]), 'Encoding': 'utf-32', 'Error': 'Failed to parse'},
    {'Name': 'invalid UTF-8', 'Bytes': b'["\xff"]', 'Error': 'Failed to parse'},
    {'Name': 'invalid pattern', 'Text': json.dumps([{**_valid_definition, 'pattern': '['}]), 'Error': 'Invalid regular expression'},
    {'Name': 'escaped error reason', 'Text': json.dumps([{**_valid_definition, 'name': '日本語 "rule"\\path\nnext', 'pattern': '['}]), 'Error': 'Invalid regular expression'},
    {'Name': 'later invalid pattern', 'Text': json.dumps([_valid_definition, {**_valid_definition, 'pattern': '['}]), 'Error': 'Invalid regular expression'},
]

for value in (None, True, 42, 'text', []):
    _definition_cases.append({
        'Name': f'non-object definition {value!r}',
        'Text': json.dumps([value]),
        'Error': 'must be a JSON object',
    })

for key in ('name', 'pattern', 'reason'):
    for value in (None, True, 42, 1.5, [], {}, '', ' \t\n'):
        _definition_cases.append({
            'Name': f'invalid {key}: {value!r}',
            'Text': json.dumps([{**_valid_definition, key: value}]),
            'Error': 'requires non-empty strings',
        })
    for replacement in (None, key.upper()):
        definition = _valid_definition.copy()
        value = definition.pop(key)
        if replacement is not None:
            definition[replacement] = value
        _definition_cases.append({
            'Name': f'missing or wrong-case {key}: {replacement}',
            'Text': json.dumps([definition]),
            'Error': 'requires non-empty strings',
        })

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class BlockCommandsTests(unittest.TestCase):
    def __invoke_hook_input(self, input_json, entry_point='py', search_path=None, definitions_file=None, wrapper_path=None, code_page=None):
        environment = os.environ.copy()
        if entry_point == 'cmd':
            environment['BLOCK_COMMANDS_TEST_HOOK'] = wrapper_path or os.path.join(_script_directory, 'block-commands.cmd')
            environment['BLOCK_COMMANDS_TEST_PYTHON'] = sys.executable
            environment['BLOCK_COMMANDS_TEST_TOKEN'] = 'unexpected-expansion'
            options = ''
            if definitions_file is not None:
                environment['BLOCK_COMMANDS_TEST_DEFINITIONS'] = definitions_file
                options = ' --definitions-path "%BLOCK_COMMANDS_TEST_DEFINITIONS%"'
            code_page_command = (
                f'"%SystemRoot%\\System32\\chcp.com" {code_page} >nul & '
                if code_page is not None else ''
            )
            arguments = [
                f'"{os.environ["ComSpec"]}"', '/d', '/v:off', '/s', '/c',
                f'"{code_page_command}"%BLOCK_COMMANDS_TEST_HOOK%"{options}"',
            ]
            environment['PATH'] = self.__python_shim_path
        elif entry_point == 'sh':
            arguments = [self.__bash_path, '--noprofile', '--norc', os.path.join(_script_directory, 'block-commands.sh').replace('\\', '/')]
            if definitions_file is not None:
                arguments.extend(['--definitions-path', definitions_file])
            environment['PATH'] = self.__python3_shim_path
        else:
            arguments = [sys.executable, _hook_path, '--definitions-path', definitions_file or _definitions_path]
        if search_path is not None:
            environment['PATH'] = search_path

        completed = subprocess.run(
            # cmd.exeの引数は独自の引用規則で解釈されるため、文字列のまま渡す。
            ' '.join(arguments) if entry_point == 'cmd' else arguments,
            input=input_json if isinstance(input_json, bytes) else input_json.encode('utf-8'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if entry_point == 'cmd' and code_page is not None else 0,
        )
        return _HookResult(
            completed.returncode,
            completed.stdout.decode('utf-8'),
            completed.stderr.decode('utf-8'),
        )

    def __invoke_hook(self, command):
        payload = {
            'toolName': 'powershell',
            'toolArgs': {'command': command},
        }

        result = self.__invoke_hook_input(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        if result.exit_code != 0 or result.error:
            raise AssertionError(f"Hook failed for command '{command}': exit {result.exit_code}, {result.error}")

        return json.loads(result.output)

    def test_commands(self):
        for test_case in _test_cases:
            with self.subTest(test_case=test_case):
                result = self.__invoke_hook(test_case['Command'])
                if result.get('permissionDecision') != test_case['Expected']:
                    raise AssertionError(f"Unexpected decision for '{test_case['Command']}': '{result.get('permissionDecision')}'")

    def test_payloads(self):
        for test_case in _payload_cases:
            with self.subTest(test_case=test_case):
                result = self.__invoke_hook_input(test_case['Json'])
                if test_case.get('Error'):
                    _assert_error_denial(result, test_case['Error'])
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

    def test_definitions(self):
        with tempfile.TemporaryDirectory() as directory:
            custom_definitions_path = os.path.join(directory, 'definitions.json')
            for test_case in _definition_cases:
                with self.subTest(test_case=test_case):
                    data = test_case.get('Bytes')
                    if data is None:
                        data = test_case['Text'].encode(test_case.get('Encoding', 'utf-8'))
                    with open(custom_definitions_path, 'wb') as file:
                        file.write(data)
                    result = self.__invoke_hook_input(
                        '{"toolName":"bash","toolArgs":"blocked"}',
                        definitions_file=custom_definitions_path,
                    )
                    if test_case.get('Error'):
                        _assert_error_denial(result, test_case['Error'])
                    else:
                        expected_output = (
                            {'permissionDecision': 'deny', 'permissionDecisionReason': _valid_definition['reason']}
                            if test_case['Expected'] else {}
                        )
                        if result.exit_code != 0 or result.error or json.loads(result.output) != expected_output:
                            raise AssertionError(f"Unexpected definition result for '{test_case['Name']}': {result!r}")
            missing_file = os.path.join(directory, 'missing.json')
            result = self.__invoke_hook_input('{}', definitions_file=missing_file)
            _assert_error_denial(result, 'definitions not found or existence could not be verified')

            result = self.__invoke_hook_input('{}', definitions_file=directory)
            _assert_error_denial(result, 'path is not a file or could not be verified as a file')

    def test_wrappers_and_paths(self):
        entry_points = []

        if os.name == 'nt':
            entry_points.append('cmd')

        if os.name == 'nt':
            git_command = shutil.which('git')
            if git_command is None:
                raise AssertionError('git was not found.')
            self.__bash_path = os.path.join(os.path.dirname(os.path.dirname(git_command)), 'bin', 'bash.exe')
        else:
            self.__bash_path = shutil.which('bash')

        if self.__bash_path and os.path.isfile(self.__bash_path):
            entry_points.append('sh')
        else:
            with self.subTest(entry_point='sh'):
                self.skipTest('A native bash executable was not found.')

        with tempfile.TemporaryDirectory() as shim_directory:
            _create_python_shims(shim_directory)
            # Windowsラッパーが呼び出すpythonは、このテストを実行しているPythonの実行ファイルを元の場所で見つけさせる。
            # 実行ファイルを別の場所へコピーすると、標準ライブラリの場所を解決できないため。
            self.__python_shim_path = os.path.dirname(sys.executable) + os.pathsep + os.environ.get('PATH', '')
            self.__python3_shim_path = shim_directory + os.pathsep + os.environ.get('PATH', '')

            for entry_point in entry_points:
                with self.subTest(entry_point=entry_point):
                    for test_case in (
                        {'Json': '{"toolName":"bash","toolArgs":{"command":"git status"}}', 'Expected': None},
                        {'Json': '{"toolName":"powershell","toolArgs":{"command":"echo 日本語; git reset"}}', 'Expected': 'deny'},
                    ):
                        result = self.__invoke_hook_input(test_case['Json'], entry_point=entry_point)
                        if result.exit_code != 0 or result.error:
                            raise AssertionError(f'Unexpected {entry_point} wrapper execution: {result!r}')
                        output = json.loads(result.output)
                        if output.get('permissionDecision') != test_case['Expected']:
                            raise AssertionError(f'Unexpected {entry_point} wrapper decision: {result!r}')

                    result = self.__invoke_hook_input('{', entry_point=entry_point)
                    _assert_error_denial(result, 'Failed to parse')

                    with tempfile.TemporaryDirectory(prefix='block commands ') as directory:
                        invalid_definitions = os.path.join(directory, 'invalid.json')
                        with open(invalid_definitions, 'w', encoding='utf-8', newline='\n') as defs_file:
                            json.dump([{**_valid_definition, 'pattern': '['}], defs_file)
                        for path, expected_error in (
                            (invalid_definitions, 'Invalid regular expression'),
                            (os.path.join(directory, 'missing.json'), 'definitions not found'),
                        ):
                            result = self.__invoke_hook_input(
                                '{"toolName":"bash","toolArgs":"git status"}',
                                entry_point=entry_point,
                                definitions_file=path,
                            )
                            _assert_error_denial(result, expected_error)

                    result = self.__invoke_hook_input('{}', entry_point=entry_point, search_path='')
                    output = json.loads(result.output)
                    if (result.exit_code != 2 or output.get('permissionDecision') != 'deny' or
                            not str(output.get('permissionDecisionReason') or '').strip() or
                            'Tool execution denied.' not in result.error):
                        raise AssertionError(f'Unexpected {entry_point} wrapper missing-python3 result: {result!r}')

            if os.name == 'nt':
                with tempfile.TemporaryDirectory() as directory:
                    helpers_directory = os.path.join(
                        directory, 'repo %BLOCK_COMMANDS_TEST_TOKEN% with spaces!',
                        'helpers',
                    )
                    copied_script_directory = os.path.join(
                        helpers_directory, 'events', 'preToolUse', 'block-commands',
                    )
                    copied_utilities_directory = os.path.join(helpers_directory, 'utilities', 'check-python-version')
                    os.makedirs(copied_script_directory)
                    os.makedirs(copied_utilities_directory)
                    for filename in ('block-commands.cmd', 'block-commands.py', 'blocked-command-definitions.json'):
                        with self.subTest(filename=filename):
                            shutil.copyfile(
                                os.path.join(_script_directory, filename),
                                os.path.join(copied_script_directory, filename),
                            )
                    shutil.copyfile(
                        os.path.join(
                            _script_directory, '..', '..', '..', 'utilities', 'check-python-version',
                            'check-python-version.py',
                        ),
                        os.path.join(copied_utilities_directory, 'check-python-version.py'),
                    )
                    copied_wrapper_path = os.path.join(copied_script_directory, 'block-commands.cmd')
                    custom_definitions_path = os.path.join(
                        copied_script_directory, 'definitions %BLOCK_COMMANDS_TEST_TOKEN%!.json',
                    )
                    with open(custom_definitions_path, 'w', encoding='utf-8', newline='\n') as file:
                        json.dump([_valid_definition], file)
                    cmd_shim_directory = os.path.join(directory, 'python cmd')
                    os.makedirs(cmd_shim_directory)
                    cmd_shim_path = os.path.join(cmd_shim_directory, 'python.cmd')
                    with open(cmd_shim_path, 'w', encoding='utf-8', newline='\n') as file:
                        file.write('@echo off\n"%BLOCK_COMMANDS_TEST_PYTHON%" %*\nexit /b %errorlevel%\n')

                    for code_page, search_path in (
                        (932, self.__python_shim_path),
                        (932, cmd_shim_directory),
                        (65001, self.__python_shim_path),
                        (65001, cmd_shim_directory),
                    ):
                        with self.subTest(code_page=code_page, search_path=search_path):
                            for command, expected_output in (
                                ('git status', {}),
                                ('blocked', {
                                    'permissionDecision': 'deny',
                                    'permissionDecisionReason': _valid_definition['reason'],
                                }),
                            ):
                                result = self.__invoke_hook_input(
                                    json.dumps({'toolName': 'bash', 'toolArgs': command}),
                                    entry_point='cmd', search_path=search_path,
                                    definitions_file=custom_definitions_path,
                                    wrapper_path=copied_wrapper_path,
                                    code_page=code_page,
                                )
                                if result.exit_code != 0 or result.error or json.loads(result.output) != expected_output:
                                    raise AssertionError(f'Unexpected cmd path/shim result on CP{code_page}: {result!r}')

                    with open(cmd_shim_path, 'w', encoding='utf-8', newline='\n') as file:
                        file.write('@echo off\nexit /b 1\n')
                    for code_page in (932, 65001):
                        with self.subTest(code_page=code_page):
                            result = self.__invoke_hook_input(
                                '{}', entry_point='cmd', search_path=cmd_shim_directory,
                                wrapper_path=copied_wrapper_path, code_page=code_page,
                            )
                            _assert_error_denial(result, 'Python 3.14 or later is required')


if __name__ == '__main__':
    run_tests()
