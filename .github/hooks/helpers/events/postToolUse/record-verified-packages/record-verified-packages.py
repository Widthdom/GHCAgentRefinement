import io
import json
import os
import re
import runpy
import sqlite3
import sys
from typing import cast


SHELL_TOOL_NAMES = {'bash', 'powershell'}
WEB_FETCH_TOOL_NAMES = {'web_fetch'}
NUGET = 'nuget'
PYPI = 'pypi'
NPM = 'npm'
PYTHON_MODULE = 'python-module'
NAME = r'(?P<name>(?:@[\w.-]+/)?[A-Za-z0-9_][\w.-]*)'
# 1つのコマンドの区切り。区切りの後は別のコマンドとして照合する。
COMMAND_SEPARATOR_PATTERN = re.compile(r'&&|\|\||[;|\n]')
# レジストリへの問い合わせと、レジストリから取得して依存に加えるコマンド。オプションは名前の前後のどちらにも置ける。
OPTIONS = r'(?:\s+-{1,2}[\w-]+(?:[=\s]+(?!-)[^\s-]\S*)?)*'
COMMAND_PATTERNS = [
    (NUGET, re.compile(rf'\bdotnet\s+package\s+search{OPTIONS}\s+{NAME}')),
    (NUGET, re.compile(rf'\bdotnet\s+add\s+(?:(?!package\b)\S+\s+)?package\s+{NAME}')),
    (NUGET, re.compile(rf'\bnuget(?:\.exe)?\s+(?:list|search){OPTIONS}\s+{NAME}')),
    (PYPI, re.compile(rf'\bpip(?:3(?:\.\d+)?)?\s+index\s+versions{OPTIONS}\s+{NAME}')),
    (PYPI, re.compile(rf'\bpip(?:3(?:\.\d+)?)?\s+download{OPTIONS}\s+{NAME}')),
    (PYPI, re.compile(rf'\buv\s+add{OPTIONS}\s+{NAME}')),
    (NPM, re.compile(rf'\b(?:npm|pnpm|yarn)\s+(?:view|info|show|v){OPTIONS}\s+{NAME}')),
    (NPM, re.compile(rf'\bpnpm\s+add{OPTIONS}\s+{NAME}')),
    (PYTHON_MODULE, re.compile(r'\bpython(?:3(?:\.\d+)?)?(?:\.exe)?\s+-c\s+["\']\s*import\s+(?P<name>[A-Za-z_]\w*)\s*["\']')),
]
# レジストリが見つからないと答えた出力。
NOT_FOUND_PATTERN = re.compile(
    r'(?i)no results|not found|\bE404\b|\b404\b|no matching distribution|could not find|unable to find package|'
    r'\bNU1101\b|no solution found|\bERR!|No module named|ModuleNotFoundError|ImportError|does not exist'
)
URL_PATTERNS = [
    (NUGET, re.compile(r'^https?://(?:www\.)?nuget\.org/packages/(?P<name>[^/?#]+)', re.IGNORECASE)),
    (NUGET, re.compile(r'^https?://api\.nuget\.org/v3(?:-flatcontainer|/registration[^/]*)/(?P<name>[^/?#]+)/', re.IGNORECASE)),
    (PYPI, re.compile(r'^https?://pypi\.org/(?:project|pypi|simple)/(?P<name>[^/?#]+)', re.IGNORECASE)),
    (NPM, re.compile(r'^https?://(?:www\.)?npmjs\.com/package/(?P<name>(?:@[^/?#]+/)?[^/?#]+)', re.IGNORECASE)),
    (NPM, re.compile(r'^https?://registry\.npmjs\.org/(?P<name>(?:@[^/?#]+(?:/|%2[fF]))?[^/?#@]+)', re.IGNORECASE)),
]
WEB_NOT_FOUND_PATTERN = re.compile(r'(?i)\b404\b|not found|does not exist')


def strip_version(ecosystem, name):
    # `name@1.2.3`・`name==1.2.3` のようにバージョンを付けた指定から名前だけを取り出す。
    if ecosystem == NPM:
        scope, separator, rest = name.partition('/') if name.startswith('@') else ('', '', name)
        return f'{scope}{separator}{rest.split("@")[0]}'
    if ecosystem == PYPI:
        return re.split(r'[<>=!~\[;]', name)[0]
    return name


def get_command_packages(command, output):
    # 戻り値は、出力が名前を含み、見つからない旨を含まないコマンドの (エコシステム, 名前) のリスト。
    if NOT_FOUND_PATTERN.search(output):
        return []
    packages = []
    for segment in COMMAND_SEPARATOR_PATTERN.split(command):
        for ecosystem, pattern in COMMAND_PATTERNS:
            match = pattern.search(segment)
            if not match:
                continue
            name = strip_version(ecosystem, match.group('name'))
            # `python -c "import x"` は成功しても何も出力しない。それ以外は、出力に名前があることを確かめる。
            if name and (ecosystem == PYTHON_MODULE or name.lower() in output.lower()):
                packages.append((ecosystem, name))
    return packages


def get_url_packages(url, output):
    if not output.strip() or WEB_NOT_FOUND_PATTERN.search(output[:2000]):
        return []
    for ecosystem, pattern in URL_PATTERNS:
        match = pattern.match(url.strip())
        if match:
            name = match.group('name').replace('%2f', '/').replace('%2F', '/')
            return [(ecosystem, strip_version(ecosystem, name))]
    return []


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'verified-packages-state.py',
        ))
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        # postToolUseには、呼び出したツール1件の名前（toolName）と引数（toolArgs）、結果（toolResult）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        tool_name = tool_name.removeprefix('functions.')
        tool_args = hook_input['toolArgs']
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except ValueError:
                return 0
        if not isinstance(tool_args, dict):
            return 0
        tool_result = hook_input.get('toolResult')
        output = tool_result.get('textResultForLlm') if isinstance(tool_result, dict) else None
        output = output if isinstance(output, str) else ''

        packages = []
        if tool_name in SHELL_TOOL_NAMES and isinstance(tool_args.get('command'), str):
            packages = get_command_packages(tool_args['command'], output)
        elif tool_name in WEB_FETCH_TOOL_NAMES and isinstance(tool_args.get('url'), str):
            packages = get_url_packages(tool_args['url'], output)
        if packages:
            state['record_verified'](state_path, packages)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to record verified packages: {ex}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
