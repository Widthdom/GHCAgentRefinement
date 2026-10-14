@echo off

where pwsh >nul 2>&1
if errorlevel 1 goto :missing

pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0block-commands.ps1" %*
exit /b %errorlevel%

:missing
>&2 echo PowerShell 7 or later is required to enforce the repository command policy. Tool execution denied.
echo {"permissionDecision":"deny","permissionDecisionReason":"PowerShell 7 or later is required to enforce the repository command policy. Tool execution denied."}
exit /b 2
