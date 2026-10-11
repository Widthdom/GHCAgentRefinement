$ErrorActionPreference = 'Stop'

$hookPath = Join-Path $PSScriptRoot '..\..\check-debug-mode.ps1'
$sessionId = [guid]::NewGuid().ToString('D')
$statePath = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    "ghc-agent-refinement\debug-mode\$sessionId.json"

function Invoke-DebugModeHook {
    param(
        [Parameter(Mandatory)]
        [string]$Prompt
    )

    @{
        sessionId = $sessionId
        prompt = $Prompt
    } |
        ConvertTo-Json -Compress |
        & powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $hookPath

    if ($LASTEXITCODE -ne 0) {
        throw "Hook exited with code $LASTEXITCODE for prompt: $Prompt"
    }
}

function Get-DebugMode {
    (Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json).enabled
}

try {
    Invoke-DebugModeHook -Prompt 'regular prompt'
    if ((Get-DebugMode) -ne $false) {
        throw 'Regular prompt enabled footer debug mode.'
    }

    Invoke-DebugModeHook -Prompt '?? debug prompt'
    if ((Get-DebugMode) -ne $true) {
        throw 'Debug prompt did not enable footer debug mode.'
    }

    Invoke-DebugModeHook -Prompt 'The final response footer is missing or incorrect. Continue the response.'
    if ((Get-DebugMode) -ne $true) {
        throw 'Footer correction prompt changed footer debug mode.'
    }

    Invoke-DebugModeHook -Prompt 'next regular prompt'
    if ((Get-DebugMode) -ne $false) {
        throw 'Following regular prompt did not disable footer debug mode.'
    }

    Write-Output 'Passed 4 check-debug-mode tests.'
} finally {
    if (Test-Path -LiteralPath $statePath) {
        Remove-Item -LiteralPath $statePath -Force
    }
}
