param(
    [string]$WingetCommand = 'winget'
)

$ErrorActionPreference = 'Stop'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$helperRoot = Join-Path $PSScriptRoot 'helpers\check-development-tools'
$checkers = @('git', 'ripgrep', 'python')
$issues = foreach ($checker in $checkers) {
    $scriptPath = Join-Path $helperRoot "check-$checker.ps1"
    @(& $scriptPath -WingetCommand $WingetCommand)
}

$issues = @($issues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if ($issues.Count -eq 0) {
    exit 0
}

$additionalContext = @"
The Windows development-tool check found items that need attention:

$($issues -join "`n")

Tell the user about these findings and show the applicable commands. Do not install or update anything automatically.
"@

@{
    additionalContext = $additionalContext
} | ConvertTo-Json -Compress
