import io
import json
import os
import re
import runpy
import sqlite3
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))


_SHELL_TOOL_NAMES = {'bash', 'powershell'}
_WEB_FETCH_TOOL_NAMES = {'web_fetch'}
_NUGET = 'nuget'
_PYPI = 'pypi'
_NPM = 'npm'
_PYTHON_MODULE = 'python-module'
_NAME = r'(?P<name>(?:@[\w.-]+/)?[A-Za-z0-9_][\w.-]*)'
# 1つのコマンドの区切り。区切りの後は別のコマンドとして照合する。
_COMMAND_SEPARATOR_PATTERN = re.compile(r'&&|\|\||[;|\n]')
# レジストリへの問い合わせと、レジストリから取得して依存に加えるコマンド。オプションは名前の前後のどちらにも置ける。
_OPTIONS = r'(?:\s+-{1,2}[\w-]+(?:[=\s]+(?!-)[^\s-]\S*)?)*'
_COMMAND_PATTERNS = [
    (_NUGET, re.compile(rf'\bdotnet\s+package\s+search{_OPTIONS}\s+{_NAME}')),
    (_NUGET, re.compile(rf'\bdotnet\s+add\s+(?:(?!package\b)\S+\s+)?package\s+{_NAME}')),
    (_NUGET, re.compile(rf'\bnuget(?:\.exe)?\s+(?:list|search){_OPTIONS}\s+{_NAME}')),
    (_PYPI, re.compile(rf'\bpip(?:3(?:\.\d+)?)?\s+index\s+versions{_OPTIONS}\s+{_NAME}')),
    (_PYPI, re.compile(rf'\bpip(?:3(?:\.\d+)?)?\s+download{_OPTIONS}\s+{_NAME}')),
    (_PYPI, re.compile(rf'\buv\s+add{_OPTIONS}\s+{_NAME}')),
    (_NPM, re.compile(rf'\b(?:npm|pnpm|yarn)\s+(?:view|info|show|v){_OPTIONS}\s+{_NAME}')),
    (_NPM, re.compile(rf'\bpnpm\s+add{_OPTIONS}\s+{_NAME}')),
    (_PYTHON_MODULE, re.compile(r'\bpython(?:3(?:\.\d+)?)?(?:\.exe)?\s+-c\s+["\']\s*import\s+(?P<name>[A-Za-z_]\w*)\s*["\']')),
]
# レジストリが見つからないと答えた出力。
_NOT_FOUND_PATTERN = re.compile(
    r'(?i)no results|not found|\bE404\b|\b404\b|no matching distribution|could not find|unable to find package|'
    r'\bNU1101\b|no solution found|\bERR!|No module named|ModuleNotFoundError|ImportError|does not exist'
)
_URL_PATTERNS = [
    (_NUGET, re.compile(r'^https?://(?:www\.)?nuget\.org/packages/(?P<name>[^/?#]+)', re.IGNORECASE)),
    (_NUGET, re.compile(r'^https?://api\.nuget\.org/v3(?:-flatcontainer|/registration[^/]*)/(?P<name>[^/?#]+)/', re.IGNORECASE)),
    (_PYPI, re.compile(r'^https?://pypi\.org/(?:project|pypi|simple)/(?P<name>[^/?#]+)', re.IGNORECASE)),
    (_NPM, re.compile(r'^https?://(?:www\.)?npmjs\.com/package/(?P<name>(?:@[^/?#]+/)?[^/?#]+)', re.IGNORECASE)),
    (_NPM, re.compile(r'^https?://registry\.npmjs\.org/(?P<name>(?:@[^/?#]+(?:/|%2[fF]))?[^/?#@]+)', re.IGNORECASE)),
]
_WEB_NOT_FOUND_PATTERN = re.compile(r'(?i)\b404\b|not found|does not exist')


def _strip_version(ecosystem, name):
    # `name@1.2.3`・`name==1.2.3` のようにバージョンを付けた指定から名前だけを取り出す。
    if ecosystem == _NPM:
        scope, separator, rest = name.partition('/') if name.startswith('@') else ('', '', name)
        return f'{scope}{separator}{rest.split("@")[0]}'
    if ecosystem == _PYPI:
        return re.split(r'[<>=!~\[;]', name)[0]
    return name


def _get_command_packages(command, output):
    # 戻り値は、出力が名前を含み、見つからない旨を含まないコマンドの (エコシステム, 名前) のリスト。
    if _NOT_FOUND_PATTERN.search(output):
        return []
    packages = []
    for segment in _COMMAND_SEPARATOR_PATTERN.split(command):
        for ecosystem, pattern in _COMMAND_PATTERNS:
            match = pattern.search(segment)
            if not match:
                continue
            name = _strip_version(ecosystem, match.group('name'))
            # `python -c "import x"` は成功しても何も出力しない。それ以外は、出力に名前があることを確かめる。
            if name and (ecosystem == _PYTHON_MODULE or name.lower() in output.lower()):
                packages.append((ecosystem, name))
    return packages


def _get_url_packages(url, output):
    if not output.strip() or _WEB_NOT_FOUND_PATTERN.search(output[:2000]):
        return []
    for ecosystem, pattern in _URL_PATTERNS:
        match = pattern.match(url.strip())
        if match:
            name = match.group('name').replace('%2f', '/').replace('%2F', '/')
            return [(ecosystem, _strip_version(ecosystem, name))]
    return []


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'verified-packages-state', 'verified-packages-state.py',
        ))
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        # postToolUseには、呼び出したツール1件の名前（toolName）と引数（toolArgs）、結果（toolResult）が渡される。
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        tool_name = tool_name.removeprefix('functions.')
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
        if tool_name in _SHELL_TOOL_NAMES and isinstance(tool_args.get('command'), str):
            packages = _get_command_packages(tool_args['command'], output)
        elif tool_name in _WEB_FETCH_TOOL_NAMES and isinstance(tool_args.get('url'), str):
            packages = _get_url_packages(tool_args['url'], output)
        if packages:
            state['record_verified'](state_path, packages)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to record verified packages: {ex}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
