param(
    [string]$WingetCommand = 'winget'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"

$packageId = 'Git.Git'
$installed = Get-ToolVersion `
    -Command 'git' `
    -Arguments @('--version') `
    -Pattern 'git version (?<version>\d+(?:\.\d+){2})(?:\.windows\.(?<revision>\d+))?'
$wingetAvailable = Test-WingetAvailable -Command $WingetCommand

if ($null -ne $installed -and $installed.Match.Groups['revision'].Success) {
    $installed.Value = ConvertTo-Version -Value (
        "$($installed.Match.Groups['version'].Value).$($installed.Match.Groups['revision'].Value)"
    )
}

if ($null -eq $installed) {
    if ($wingetAvailable) {
        "- Git: not installed. Install: winget install --id $packageId --exact --source winget"
    } else {
        '- Git: not installed. winget is unavailable, so no install command can be suggested.'
    }
    return
}

if (-not $wingetAvailable) {
    "- Git: installed ($($installed.Display)), but the latest version could not be checked because winget is unavailable."
    return
}

$latest = Get-LatestWingetPackageVersion -WingetCommand $WingetCommand -PackageId $packageId
if ($null -eq $latest) {
    "- Git: installed ($($installed.Display)), but the latest version check failed."
    return
}

if ($installed.Value -lt $latest.Value) {
    $managed = Test-WingetPackageInstalled -WingetCommand $WingetCommand -PackageId $packageId
    $command = if ($managed) {
        "winget upgrade --id $packageId --exact --source winget"
    } else {
        "winget install --id $packageId --exact --source winget"
    }
    "- Git: update available ($($installed.Display) -> $($latest.Display)). Update: $command"
}
