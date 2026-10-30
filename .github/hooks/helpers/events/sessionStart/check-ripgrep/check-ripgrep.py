import io
import json
import re
import subprocess
import sys
from typing import cast

# Ubuntu 22.04 LTSの標準パッケージ（13.0.0）を基準にする。
MINIMUM_VERSION = (13, 0, 0)
ADDITIONAL_CONTEXT = (
    '{message}\n'
    'At the start of your first response, tell the user about this in the language you respond in, '
    'including how to install it. Do not install it yourself.'
)


def is_requirement_satisfied():
    # 例：'ripgrep 14.1.1 (rev 4649aa9700)'
    try:
        # capture_output：標準出力と標準エラーをキャプチャするための引数。
        #   Trueに設定すると、subprocess.runの戻り値のstdoutおよびstderr属性に出力が格納される。末尾の.stdoutによってその標準出力を取得している。
        # text=True：標準出力と標準エラーが文字列として取得される。
        # errors='replace'：標準出力や標準エラーの文字エンコーディングエラーが発生した場合に、 ? に置き換える。
        output = subprocess.run(
            ['rg', '--version'], capture_output=True, text=True, errors='replace', check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    # r'...' はPythonの raw文字列リテラルで、バックスラッシュがエスケープ文字として扱われない。C#の@""に相当する。
    match = re.match(r'ripgrep (\d+)\.(\d+)\.(\d+)', output)
    return match is not None and tuple(int(number) for number in match.groups()) >= MINIMUM_VERSION


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8')
    if is_requirement_satisfied():
        return 0

    lines = ['ripgrep 13.0.0 or later is required, and it must be available as the rg command.']
    if sys.platform == 'win32':
        lines.append('Install it with: winget install --id BurntSushi.ripgrep.MSVC --exact --source winget')
    elif sys.platform == 'darwin':
        lines.append('Install it with: brew install ripgrep')
    else:
        lines.append('Installation guide: https://github.com/BurntSushi/ripgrep#installation')
    lines.append('After installation, restart the application or terminal running this hook to refresh PATH.')
    message = '\n'.join(lines)
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
