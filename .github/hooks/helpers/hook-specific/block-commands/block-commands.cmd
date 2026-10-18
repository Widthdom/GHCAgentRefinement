@echo off

python "%~dp0..\..\common\check-python-version.py" >nul 2>&1
if errorlevel 1 (
    echo Python 3.14 or later is required to enforce the repository command policy. Tool execution denied.>&2
    echo {"permissionDecision":"deny","permissionDecisionReason":"Python 3.14 or later is required to enforce the repository command policy. Tool execution denied."}
    exit /b 2
)

python "%~dp0block-commands.py" %*
exit /b %errorlevel%
