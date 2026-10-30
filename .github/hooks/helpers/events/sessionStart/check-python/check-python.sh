#!/bin/sh

# 2>&1：標準エラー出力を、標準出力と同じ出力先にする
# 1>&2：標準出力を、標準エラー出力と同じ出力先にする（>&2 と省略可能）
# 1：標準出力
# 2：標準エラー出力

# 1>/dev/null 2>&1：標準出力の出力先を/dev/nullにし、標準エラー出力も標準出力と同じ出力先（左記により/dev/null）にする
python3 "${0%/*}/../../../utilities/check-python-version.py" 1>/dev/null 2>&1
if [ "$?" -ne 0 ]; then
    requirement='Python 3.14 or later is required, and it must be available as the python3 command.'
    # macOS の場合は Homebrew でのインストールを推奨
    if [ "$(uname -s 2>/dev/null)" = 'Darwin' ]; then
        installation='Install it with: brew install python'
    # macOS 以外の場合は公式インストールガイド参照を推奨
    else
        installation='Installation guide: https://docs.python.org/3/using/unix.html'
    fi
    restart='If python3 is still unavailable after installation, restart the application or terminal running this hook to refresh PATH.'
    printf '%s\n%s\n%s\n' "$requirement" "$installation" "$restart" >&2
    # 標準エラーはユーザーに表示されないことがある。sessionStartで解釈される出力はadditionalContextだけで、
    # 標準出力は終了コード0のときだけ読まれるため、エージェントに渡してユーザーへ伝えさせ、正常終了する。
    # Pythonを使えないためJSONは手で組み立てる。案内の文言には、JSONでエスケープが必要な " と \ を含めない。
    printf '{"additionalContext":"%s\\n%s\\n%s\\n%s"}\n' "$requirement" "$installation" "$restart" \
        'At the start of your first response, tell the user about this in the language you respond in, including how to install it. Do not install it yourself.'
    exit 0
fi
exit 0
