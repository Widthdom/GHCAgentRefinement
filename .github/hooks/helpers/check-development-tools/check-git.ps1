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
. "$PSScriptRoot\common.ps1"

$resolvedPlatform = Get-DevelopmentPlatform -Platform $Platform
$packageManager = Get-DevelopmentPackageManager @PSBoundParameters
$packageId = if ($resolvedPlatform -eq 'Windows') { 'Git.Git' } else { 'git' }
$installed = Get-ToolVersion `
    -Command 'git' `
    -Arguments @('--version') `
    -Pattern 'git version (?<version>\d+(?:\.\d+){2})(?:\.windows\.(?<revision>\d+))?'
if ($null -ne $installed -and $installed.Match.Groups['revision'].Success) {
    $installed.Value = ConvertTo-Version -Value (
        "$($installed.Match.Groups['version'].Value).$($installed.Match.Groups['revision'].Value)"
    )
}

if ($null -eq $installed) {
    if ($null -ne $packageManager) {
        $command = Get-PackageInstallCommand -PackageManager $packageManager -PackageId $packageId
        "- Git: not installed. Install: $command"
    } else {
        "- Git: not installed. No supported package manager is available for $resolvedPlatform, so no install command can be suggested."
    }
    return
}

if ($null -eq $packageManager) {
    "- Git: installed ($($installed.Display)), but the latest version could not be checked because no supported package manager is available for $resolvedPlatform."
    return
}

$latest = Get-LatestPackageVersion -PackageManager $packageManager -PackageId $packageId
if ($null -eq $latest) {
    "- Git: installed ($($installed.Display)), but the latest version check failed."
    return
}

if ($installed.Value -lt $latest.Value) {
    $managed = Test-PackageManaged -PackageManager $packageManager -PackageId $packageId
    $command = Get-PackageInstallCommand `
        -PackageManager $packageManager `
        -PackageId $packageId `
        -Upgrade:$managed
    "- Git: update available ($($installed.Display) -> $($latest.Display)). Update: $command"
}
