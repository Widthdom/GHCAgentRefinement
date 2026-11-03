import argparse
import io
import json
import os
import re
import subprocess
import sys
from typing import cast

ADDITIONAL_CONTEXT = (
    '{message}\n'
    'At the start of your first response, tell the user about this in the language you respond in, '
    'including how to install the tools. Do not install them yourself.'
)
RESTART_MESSAGE = 'After installation, restart the application or terminal running this hook to refresh PATH.'
PLATFORMS = ('win32', 'darwin', 'other')


def parse_version(text):
    # 例：'2.34.1'、'1.6'（パッチ番号を省略した版は0とみなす）。
    if not isinstance(text, str) or not re.fullmatch(r'\d+(?:\.\d+){0,2}', text):
        raise ValueError(f'invalid version: {text!r}')
    numbers = [int(number) for number in text.split('.')]
    return tuple(numbers + [0] * (3 - len(numbers)))


def load_definitions(path):
    # 戻り値は {name, command, version_pattern, minimum_version（Noneなら存在だけを確かめる）, install} のリスト。
    with open(path, encoding='utf-8') as file:
        definitions = json.load(file)
    tools = definitions.get('tools') if isinstance(definitions, dict) else None
    if not isinstance(tools, list) or not tools:
        raise ValueError('required tool definitions require a non-empty array for tools.')
    loaded = []
    for tool in tools:
        if not isinstance(tool, dict) or not all(
            isinstance(tool.get(key), str) and tool[key].strip() for key in ('name', 'command', 'versionPattern')
        ):
            raise ValueError('each required tool requires non-empty strings for name, command, and versionPattern.')
        install = tool.get('install')
        if not isinstance(install, dict) or not all(
            isinstance(install.get(platform), str) and install[platform].strip() for platform in PLATFORMS
        ):
            raise ValueError(f'required tool {tool["name"]} requires install messages for {", ".join(PLATFORMS)}.')
        try:
            version_pattern = re.compile(tool['versionPattern'])
        except re.error as ex:
            raise ValueError(f'invalid versionPattern for {tool["name"]}: {ex}') from None
        minimum_version = tool.get('minimumVersion')
        loaded.append({
            'name': tool['name'],
            'command': tool['command'],
            'version_pattern': version_pattern,
            'minimum_version': None if minimum_version is None else parse_version(minimum_version),
            'minimum_version_text': minimum_version,
            'install': install,
        })
    return loaded


def is_requirement_satisfied(tool):
    try:
        # capture_output：標準出力と標準エラーをキャプチャするための引数。
        #   Trueに設定すると、subprocess.runの戻り値のstdoutおよびstderr属性に出力が格納される。末尾の.stdoutによってその標準出力を取得している。
        # text=True：標準出力と標準エラーが文字列として取得される。
        # errors='replace'：標準出力や標準エラーの文字エンコーディングエラーが発生した場合に、 ? に置き換える。
        output = subprocess.run(
            [tool['command'], '--version'], capture_output=True, text=True, errors='replace', check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    match = tool['version_pattern'].match(output)
    if match is None:
        return False
    if tool['minimum_version'] is None:
        return True
    # パッチ番号などを省略した版は、省略した番号を0とみなして比べる。
    version = tuple(int(number or 0) for number in match.groups())
    return version + (0,) * (3 - len(version)) >= tool['minimum_version']


def get_requirement_message(tool):
    platform = sys.platform if sys.platform in ('win32', 'darwin') else 'other'
    minimum = f' {tool["minimum_version_text"]} or later' if tool['minimum_version'] is not None else ''
    return (
        f'{tool["name"]}{minimum} is required, and it must be available as the {tool["command"]} command.\n'
        f'{tool["install"][platform]}'
    )


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'required-tool-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)
    try:
        tools = load_definitions(parsed_arguments.definitions_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to load required tool definitions: {ex}', file=sys.stderr)
        return 2

    missing = [get_requirement_message(tool) for tool in tools if not is_requirement_satisfied(tool)]
    if not missing:
        return 0

    message = '\n'.join([*missing, RESTART_MESSAGE])
    print(message, file=sys.stderr)
    # 標準エラーはユーザーに表示されないことがある。sessionStartで解釈される出力はadditionalContextだけで、
    # 標準出力は終了コード0のときだけ読まれるため、エージェントに渡してユーザーへ伝えさせ、正常終了する。
    print(json.dumps(
        {'additionalContext': ADDITIONAL_CONTEXT.format(message=message)},
        ensure_ascii=False, separators=(',', ':'),
    ))
    return 0


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
