import re
import subprocess
import sys

# Ubuntu 22.04 LTSの標準パッケージ（13.0.0）を基準にする。
MINIMUM_VERSION = (13, 0, 0)


def is_requirement_satisfied():
    # 例：'ripgrep 14.1.1 (rev 4649aa9700)'
    try:
        # capture_output：標準出力と標準エラーをキャプチャするための引数。
        #   Trueに設定すると、subprocess.runの戻り値のstdoutおよびstderr属性に出力が格納される。末尾の.stdoutによってその標準出力を取得している。
        # text=True：標準出力と標準エラーが文字列として取得される。
        # errors='replace'：標準出力や標準エラーの文字エンコーディングエラーが発生した場合に、 ? に置き換える。
        output = subprocess.run(['rg', '--version'], capture_output=True, text=True, errors='replace').stdout
    except OSError:
        return False
    # r'...' はPythonの raw文字列リテラルで、バックスラッシュがエスケープ文字として扱われない。C#の@""に相当する。
    match = re.match(r'ripgrep (\d+)\.(\d+)\.(\d+)', output)
    return match is not None and tuple(int(number) for number in match.groups()) >= MINIMUM_VERSION


def main():
    sys.stderr.reconfigure(encoding='utf-8')
    if is_requirement_satisfied():
        return 0

    print('ripgrep 13.0.0 or later is required, and it must be available as the rg command.', file=sys.stderr)
    if sys.platform == 'win32':
        print('Install it with: winget install --id BurntSushi.ripgrep.MSVC --exact --source winget', file=sys.stderr)
    elif sys.platform == 'darwin':
        print('Install it with: brew install ripgrep', file=sys.stderr)
    else:
        print('Installation guide: https://github.com/BurntSushi/ripgrep#installation', file=sys.stderr)
    print('After installation, restart the application or terminal running this hook to refresh PATH.', file=sys.stderr)
    return 2


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
