@echo off

set "check_python_version_path=%~dp0..\..\..\utilities\check-python-version\check-python-version.py"

call python "%%check_python_version_path%%" 1>nul 2>&1
if %errorlevel% geq 1 (
    echo Python 3.14 or later is required, and it must be available as the python command.>&2
    echo Install it with: winget install Python.Python.3.14 --source winget>&2
    echo After installation, restart the application or terminal running this hook to refresh PATH.>&2
    rem stderr may not be shown to the user. sessionStart only reads additionalContext from stdout on exit code 0,
    rem so pass the notice to the agent to tell the user. The JSON is built by hand because Python is unavailable;
    rem keep the text free of characters that JSON or cmd would need to escape.
    echo {"additionalContext":"Python 3.14 or later is required, and it must be available as the python command.\nInstall it with: winget install Python.Python.3.14 --source winget\nAfter installation, restart the application or terminal running this hook to refresh PATH.\nAt the start of your first response, tell the user about this in the language you respond in, including how to install it. Do not install it yourself."}
    exit /b 0
)
exit /b 0
