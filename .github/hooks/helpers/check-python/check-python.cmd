@echo off

where python >nul 2>&1
if errorlevel 1 goto :missing

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)" >nul 2>&1
if not errorlevel 1 exit /b 0

:missing
>&2 echo Python 3.14 or later is required, and it must be available as the python command.
>&2 echo Install Python 3.14 with: winget install Python.Python.3.14 --source winget
>&2 echo After installation, restart the application or terminal running this hook to refresh PATH.
>&2 echo Installation guide: https://docs.python.org/3/using/windows.html
exit /b 2
