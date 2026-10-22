@echo off

rem 2>&1：標準エラー出力を、標準出力と同じ出力先にする
rem 1>&2：標準出力を、標準エラー出力と同じ出力先にする（>&2 と省略可能）
rem 1：標準出力
rem 2：標準エラー出力

rem 1>nul 2>&1：標準出力の出力先をnulにし、標準エラー出力も標準出力と同じ出力先（左記によりnul）にする
python "%~dp0..\..\..\utilities\check-python-version.py" 1>nul 2>&1
if %errorlevel% geq 1 (
    echo Python 3.14 or later is required to enforce the repository command policy. Tool execution denied.>&2
    rem JSON 形式で標準出力することで、hook実行元に対して許可拒否とその理由を伝える
    echo {"permissionDecision":"deny","permissionDecisionReason":"Python 3.14 or later is required to enforce the repository command policy. Tool execution denied."}
    exit /b 2
)

rem %*：本クリプトに渡された引数全体を引用符も含め展開する（"%*" としてしまうと引数の区切りや引用符の解釈を壊す可能性がある。）
python "%~dp0block-commands.py" %*
exit /b %errorlevel%
