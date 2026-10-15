@echo off

where python >nul 2>&1
if errorlevel 1 goto :missing

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)" >nul 2>&1
if errorlevel 1 goto :missing

python "%~dp0block-commands.py" %*
exit /b %errorlevel%

:missing
>&2 echo Python 3.14 or later is required to enforce the repository command policy. Tool execution denied.
echo {"permissionDecision":"deny","permissionDecisionReason":"Python 3.14 or later (python) is required to enforce the repository command policy. Tool execution denied."}
exit /b 2
