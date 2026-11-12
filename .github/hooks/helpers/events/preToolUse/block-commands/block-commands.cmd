@echo off

set "check_python_version_path=%~dp0..\..\..\utilities\check-python-version\check-python-version.py"

call python "%%check_python_version_path%%" 1>nul 2>&1
rem Python can crash with a negative exit code (e.g. 0xC0000135), so treat any nonzero code as unavailable.
if %errorlevel% neq 0 (
    echo Python 3.14 or later is required to enforce the repository command policy. Tool execution denied.>&2
    echo {"permissionDecision":"deny","permissionDecisionReason":"Python 3.14 or later is required to enforce the repository command policy. Tool execution denied."}
    exit /b 2
)

rem  %*: represents all the arguments passed to this script.
python "%~dp0block-commands.py" %*
exit /b %errorlevel%
