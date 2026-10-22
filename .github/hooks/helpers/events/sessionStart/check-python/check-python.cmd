@echo off

rem 2>&1：標準エラー出力を、標準出力と同じ出力先にする
rem 1>&2：標準出力を、標準エラー出力と同じ出力先にする（>&2 と省略可能）
rem 1：標準出力
rem 2：標準エラー出力

rem 1>nul 2>&1：標準出力の出力先をnulにし、標準エラー出力も標準出力と同じ出力先（左記によりnul）にする
python "%~dp0..\..\..\utilities\check-python-version.py" 1>nul 2>&1
if %errorlevel% geq 1 (
    echo Python 3.14 or later is required, and it must be available as the python command.>&2
    echo Install it with: winget install Python.Python.3.14 --source winget>&2
    echo After installation, restart the application or terminal running this hook to refresh PATH.>&2
    exit /b 2
)
exit /b 0
