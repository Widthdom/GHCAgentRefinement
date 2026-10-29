import re
import subprocess
import sys

# Ubuntu 22.04 LTSの標準パッケージ（1.6）を基準にする。
MINIMUM_VERSION = (1, 6, 0)


def is_requirement_satisfied():
    # 例：'jq-1.7.1'、'jq-1.6'（パッチ番号を省略する版がある）
    try:
        # capture_output：標準出力と標準エラーをキャプチャするための引数。
        #   Trueに設定すると、subprocess.runの戻り値のstdoutおよびstderr属性に出力が格納される。末尾の.stdoutによってその標準出力を取得している。
        # text=True：標準出力と標準エラーが文字列として取得される。
        # errors='replace'：標準出力や標準エラーの文字エンコーディングエラーが発生した場合に、 ? に置き換える。
        output = subprocess.run(
            ['jq', '--version'], capture_output=True, text=True, errors='replace', check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    # r'...' はPythonの raw文字列リテラルで、バックスラッシュがエスケープ文字として扱われない。C#の@""に相当する。
    match = re.match(r'jq-(\d+)\.(\d+)(?:\.(\d+))?', output)
    return match is not None and tuple(int(number or 0) for number in match.groups()) >= MINIMUM_VERSION


def main():
    sys.stderr.reconfigure(encoding='utf-8')
    if is_requirement_satisfied():
        return 0

    print('jq 1.6 or later is required, and it must be available as the jq command.', file=sys.stderr)
    if sys.platform == 'win32':
        print('Install it with: winget install --id jqlang.jq --exact --source winget', file=sys.stderr)
    elif sys.platform == 'darwin':
        print('Install it with: brew install jq', file=sys.stderr)
    else:
        print('Installation guide: https://github.com/jqlang/jq#installation', file=sys.stderr)
    print('After installation, restart the application or terminal running this hook to refresh PATH.', file=sys.stderr)
    return 2


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
