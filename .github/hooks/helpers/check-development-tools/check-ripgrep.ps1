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
$packageId = if ($resolvedPlatform -eq 'Windows') {
    'BurntSushi.ripgrep.MSVC'
} else {
    'ripgrep'
}
$installed = Get-ToolVersion `
    -Command 'rg' `
    -Arguments @('--version') `
    -Pattern 'ripgrep (?<version>\d+(?:\.\d+){2,3})'
if ($null -eq $installed) {
    if ($null -ne $packageManager) {
        $command = Get-PackageInstallCommand -PackageManager $packageManager -PackageId $packageId
        "- ripgrep: not installed. Install: $command"
    } else {
        "- ripgrep: not installed. No supported package manager is available for $resolvedPlatform, so no install command can be suggested."
    }
    return
}

$vsCodePackageId = if ($installed.Source -match '[\\/]Microsoft VS Code Insiders[\\/]') {
    'Microsoft.VisualStudioCode.Insiders'
} elseif ($installed.Source -match '[\\/]Microsoft VS Code(?:\.app)?[\\/]') {
    'Microsoft.VisualStudioCode'
} else {
    $null
}

if ($null -ne $vsCodePackageId) {
    if ($resolvedPlatform -ne 'Windows' -or $null -eq $packageManager) {
        "- ripgrep: available through VS Code ($($installed.Display)). Update VS Code through its built-in updater to keep the bundled version current."
        return
    }

    $vsCodeIsManaged = Test-PackageManaged `
        -PackageManager $packageManager `
        -PackageId $vsCodePackageId
    if ($vsCodeIsManaged -and
        (Test-WingetPackageUpdateAvailable -WingetCommand $WingetCommand -PackageId $vsCodePackageId)) {
        "- ripgrep: available through VS Code ($($installed.Display)); a VS Code update is available. Update: winget upgrade --id $vsCodePackageId --exact --source winget"
    }
    return
}

if ($null -eq $packageManager) {
    "- ripgrep: installed ($($installed.Display)), but the latest version could not be checked because no supported package manager is available for $resolvedPlatform."
    return
}

$latest = Get-LatestPackageVersion -PackageManager $packageManager -PackageId $packageId
if ($null -eq $latest) {
    "- ripgrep: installed ($($installed.Display)), but the latest version check failed."
    return
}

if ($installed.Value -lt $latest.Value) {
    $managed = Test-PackageManaged -PackageManager $packageManager -PackageId $packageId
    $command = Get-PackageInstallCommand `
        -PackageManager $packageManager `
        -PackageId $packageId `
        -Upgrade:$managed
    "- ripgrep: update available ($($installed.Display) -> $($latest.Display)). Update: $command"
}
