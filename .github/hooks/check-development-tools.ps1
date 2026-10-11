param(
    [ValidateSet('Auto', 'Windows', 'MacOS', 'Linux')]
    [string]$Platform = 'Auto',
    [string]$WingetCommand = 'winget',
    [string]$BrewCommand = 'brew',
    [string]$AptCommand = 'apt-cache',
    [string]$DnfCommand = 'dnf',
    [string]$PacmanCommand = 'pacman'
)

$ErrorActionPreference = 'Stop'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$helperRoot = Join-Path $PSScriptRoot 'helpers\check-development-tools'
. (Join-Path $helperRoot 'common.ps1')
$checkers = @('git', 'ripgrep', 'python')
$issues = foreach ($checker in $checkers) {
    $scriptPath = Join-Path $helperRoot "check-$checker.ps1"
    @(& $scriptPath `
        -Platform $Platform `
        -WingetCommand $WingetCommand `
        -BrewCommand $BrewCommand `
        -AptCommand $AptCommand `
        -DnfCommand $DnfCommand `
        -PacmanCommand $PacmanCommand)
}

$issues = @($issues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if ($issues.Count -eq 0) {
    exit 0
}

$resolvedPlatform = Get-DevelopmentPlatform -Platform $Platform
$platformDisplayName = if ($resolvedPlatform -eq 'MacOS') { 'macOS' } else { $resolvedPlatform }
$additionalContext = @"
The $platformDisplayName development-tool check found items that need attention:

$($issues -join "`n")

Tell the user about these findings and show the applicable commands. Do not install or update anything automatically.
"@

@{
    additionalContext = $additionalContext
} | ConvertTo-Json -Compress
