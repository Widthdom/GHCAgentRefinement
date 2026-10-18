@echo off

python "%~dp0..\..\common\check-python-version.py" >nul 2>&1
if errorlevel 1 (
    >&2 echo Python 3.14 or later is required, and it must be available as the python command.
    >&2 echo Install it with: winget install Python.Python.3.14 --source winget
    >&2 echo After installation, restart the application or terminal running this hook to refresh PATH.
    exit /b 2
)
exit /b 0
