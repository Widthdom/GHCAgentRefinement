@echo off

where pwsh >nul 2>&1
if errorlevel 1 goto :missing

pwsh -NoProfile -NonInteractive -Command "if ($PSVersionTable.PSVersion.Major -ge 7) { exit 0 } else { exit 1 }"
if not errorlevel 1 exit /b 0

:missing
>&2 echo PowerShell 7 or later is required.
>&2 echo Install it with: winget install --id Microsoft.PowerShell --exact --source winget
>&2 echo Installation guide: https://aka.ms/powershell
exit /b 2
