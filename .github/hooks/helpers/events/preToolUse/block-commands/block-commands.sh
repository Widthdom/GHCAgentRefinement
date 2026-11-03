#!/bin/sh

# 2>&1：標準エラー出力を、標準出力と同じ出力先にする
# 1>&2：標準出力を、標準エラー出力と同じ出力先にする（>&2 と省略可能）
# 1：標準出力
# 2：標準エラー出力

# 1>/dev/null 2>&1：標準出力の出力先を/dev/nullにし、標準エラー出力も標準出力と同じ出力先（左記により/dev/null）にする
python3 "${0%/*}/../../../utilities/check-python-version/check-python-version.py" 1>/dev/null 2>&1
if [ "$?" -ne 0 ]; then
    echo 'Python 3.14 or later is required to enforce the repository command policy. Tool execution denied.' >&2
    # JSON 形式で標準出力することで、hook実行元に対して許可拒否とその理由を伝える
    echo '{"permissionDecision":"deny","permissionDecisionReason":"Python 3.14 or later is required to enforce the repository command policy. Tool execution denied."}'
    exit 2
fi

# "$@"：本スクリプトに渡された各引数を個別にダブルクオートで囲んで展開する
python3 "${0%/*}/block-commands.py" "$@"
exit $?
