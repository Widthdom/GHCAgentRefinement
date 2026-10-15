import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'load-copilot-instructions.py')
github_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
real_instructions_path = os.path.join(github_directory, 'copilot-instructions.md')


def invoke_hook(script_path):
    # BOMの有無を検査できるよう、標準出力はバイト列のまま受け取る。
    completed = subprocess.run(
        [sys.executable, script_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        'ExitCode': completed.returncode,
        'OutputBytes': completed.stdout,
        'Output': completed.stdout.decode('utf-8'),
        'Error': completed.stderr.decode('utf-8'),
    }


# 本物と同じ階層（.github/hooks/helpers/load-copilot-instructions）を一時領域に再現し、
# 実ファイルに触れずに指示ファイルの有無や内容を差し替えて検証する。
def new_hook_sandbox():
    root = os.path.join(tempfile.gettempdir(), f'ghc-agent-refinement-test-{uuid.uuid4().hex}')
    hook_directory = os.path.join(root, '.github', 'hooks', 'helpers', 'load-copilot-instructions')
    os.makedirs(hook_directory, exist_ok=True)
    shutil.copy(hook_path, hook_directory)
    return {
        'Root': root,
        'ScriptPath': os.path.join(hook_directory, 'load-copilot-instructions.py'),
        'InstructionsPath': os.path.join(root, '.github', 'copilot-instructions.md'),
    }


def assert_valid_output(name, result, expected_context):
    if result['ExitCode'] != 0 or result['Error']:
        raise AssertionError(f"Hook failed for '{name}': exit {result['ExitCode']}, stderr: {result['Error']}")
    if result['OutputBytes'][:3] == b'\xef\xbb\xbf':
        raise AssertionError(f"Output contains a UTF-8 BOM for '{name}'.")
    lines = [line for line in re.split(r'\r?\n', result['Output']) if line != '']
    if len(lines) != 1:
        raise AssertionError(f"Output is not a single JSON line for '{name}': {result['Output']}")
    output = json.loads(lines[0])
    property_names = list(output.keys())
    if len(property_names) != 1 or property_names[0] != 'additionalContext':
        raise AssertionError(f"Unexpected output properties for '{name}': {', '.join(property_names)}")
    if output['additionalContext'] != expected_context:
        raise AssertionError(f"additionalContext does not match the instructions file for '{name}'.")


sandboxes = []
tests_passed = 0

try:
    # 実際の指示ファイルがそのまま渡る。
    with open(real_instructions_path, 'rb') as file:
        expected = file.read().decode('utf-8-sig')
    assert_valid_output('real instructions file', invoke_hook(hook_path), expected)
    tests_passed += 1

    valid_cases = [
        {'Name': 'Japanese text', 'Content': '# 指示\n- 応答は日本語を用いる。\n', 'Bytes': None},
        {'Name': 'CRLF line endings', 'Content': 'line1\r\nline2\r\n', 'Bytes': None},
        {'Name': 'JSON special characters', 'Content': "quote \" backslash \\ tab \t slash / <tag> & 'single'", 'Bytes': None},
        {'Name': 'emoji (surrogate pair)', 'Content': '絵文字 😀 を含む', 'Bytes': None},
        {'Name': 'UTF-8 BOM is not part of the content', 'Content': '# 指示\n',
         'Bytes': b'\xef\xbb\xbf' + '# 指示\n'.encode('utf-8')},
    ]
    for case in valid_cases:
        sandbox = new_hook_sandbox()
        sandboxes.append(sandbox)
        data = case['Bytes'] if case['Bytes'] is not None else case['Content'].encode('utf-8')
        with open(sandbox['InstructionsPath'], 'wb') as file:
            file.write(data)
        assert_valid_output(case['Name'], invoke_hook(sandbox['ScriptPath']), case['Content'])
        tests_passed += 1

    invalid_cases = [
        {'Name': 'missing instructions file', 'Bytes': None, 'Error': 'instructions file not found'},
        {'Name': 'empty instructions file', 'Bytes': b'', 'Error': 'instructions file is empty'},
        {'Name': 'whitespace-only instructions file', 'Bytes': ' \t\r\n'.encode('utf-8'), 'Error': 'instructions file is empty'},
        {'Name': 'BOM-only instructions file', 'Bytes': b'\xef\xbb\xbf', 'Error': 'instructions file is empty'},
    ]
    for case in invalid_cases:
        sandbox = new_hook_sandbox()
        sandboxes.append(sandbox)
        if case['Bytes'] is not None:
            with open(sandbox['InstructionsPath'], 'wb') as file:
                file.write(case['Bytes'])
        result = invoke_hook(sandbox['ScriptPath'])
        if result['ExitCode'] != 2 or result['Output'] or re.search(case['Error'], result['Error'], re.IGNORECASE) is None:
            raise AssertionError(
                f"Unexpected result for '{case['Name']}': exit {result['ExitCode']}, "
                f"stdout: {result['Output']}, stderr: {result['Error']}")
        tests_passed += 1

    # ディレクトリは「ファイルとして存在する」条件を満たさないため、未検出として拒否される。
    sandbox = new_hook_sandbox()
    sandboxes.append(sandbox)
    os.mkdir(sandbox['InstructionsPath'])
    result = invoke_hook(sandbox['ScriptPath'])
    if result['ExitCode'] != 2 or result['Output'] or \
            re.search('instructions file not found', result['Error'], re.IGNORECASE) is None:
        raise AssertionError(
            f"Unexpected result for 'directory instead of file': exit {result['ExitCode']}, "
            f"stdout: {result['Output']}, stderr: {result['Error']}")
    tests_passed += 1

    print(f'Passed {tests_passed} load-copilot-instructions tests.')
finally:
    for sandbox in sandboxes:
        if os.path.exists(sandbox['Root']):
            shutil.rmtree(sandbox['Root'])
