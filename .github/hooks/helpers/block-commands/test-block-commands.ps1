$ErrorActionPreference = 'Stop'

$hookPath = Join-Path $PSScriptRoot 'block-commands.ps1'
$definitionsPath = Join-Path $PSScriptRoot 'blocked-command-patterns.json'

function Invoke-Hook {
    param(
        [Parameter(Mandatory)]
        [string]$Command,

        [switch]$UseVsCodePayload
    )

    $payload = if ($UseVsCodePayload) {
        @{
            tool_name = 'Bash'
            tool_input = @{ command = $Command }
        }
    } else {
        @{
            toolName = 'powershell'
            toolArgs = @{ command = $Command }
        }
    }

    $output = $payload |
        ConvertTo-Json -Compress |
        & pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File $hookPath `
            -DefinitionsPath $definitionsPath

    if ($LASTEXITCODE -ne 0) {
        throw "Hook exited with code $LASTEXITCODE for command: $Command"
    }

    $output | ConvertFrom-Json
}

$testCases = @(
    @{ Command = 'git status'; Expected = $null },
    @{ Command = 'git rebase main'; Expected = 'deny' },
    @{ Command = 'git -C repo rebase --interactive HEAD~2'; Expected = 'deny' },
    @{ Command = 'Write-Host start; git rebase main'; Expected = 'deny' },
    @{ Command = 'git push origin main'; Expected = $null },
    @{ Command = 'git push --force origin main'; Expected = 'deny' },
    @{ Command = 'git push origin main --force-with-lease'; Expected = 'deny' },
    @{ Command = 'git -C repo push -f origin main'; Expected = 'deny' },
    @{ Command = 'Write-Host start; git push --force-if-includes origin main'; Expected = 'deny' },
    @{ Command = 'git reset'; Expected = 'deny' },
    @{ Command = 'git reset --hard HEAD~1'; Expected = 'deny' },
    @{ Command = 'git -C repo reset --soft HEAD^'; Expected = 'deny' },
    @{ Command = 'Write-Host start; git reset HEAD file.txt'; Expected = 'deny' }
)

foreach ($testCase in $testCases) {
    $result = Invoke-Hook -Command $testCase.Command
    if ($result.permissionDecision -ne $testCase.Expected) {
        throw "Unexpected decision for '$($testCase.Command)': '$($result.permissionDecision)'"
    }
}

$vsCodeResult = Invoke-Hook -Command 'git rebase main' -UseVsCodePayload
if ($vsCodeResult.permissionDecision -ne 'deny') {
    throw 'VS Code-compatible payload was not denied.'
}

Write-Output "Passed $($testCases.Count + 1) block-command hook tests."
