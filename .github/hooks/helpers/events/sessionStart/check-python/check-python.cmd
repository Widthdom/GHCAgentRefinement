@echo off

set "check_python_version_path=%~dp0..\..\..\utilities\check-python-version.py"

call python "%%check_python_version_path%%" 1>nul 2>&1
if %errorlevel% geq 1 (
    echo Python 3.14 or later is required, and it must be available as the python command.>&2
    echo Install it with: winget install Python.Python.3.14 --source winget>&2
    echo After installation, restart the application or terminal running this hook to refresh PATH.>&2
    exit /b 2
)
exit /b 0
