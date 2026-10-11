param(
    [string]$WingetCommand = 'winget'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"

$packageId = 'BurntSushi.ripgrep.MSVC'
$installed = Get-ToolVersion `
    -Command 'rg' `
    -Arguments @('--version') `
    -Pattern 'ripgrep (?<version>\d+(?:\.\d+){2,3})'
$wingetAvailable = Test-WingetAvailable -Command $WingetCommand

if ($null -eq $installed) {
    if ($wingetAvailable) {
        "- ripgrep: not installed. Install: winget install --id $packageId --exact --source winget"
    } else {
        '- ripgrep: not installed. winget is unavailable, so no install command can be suggested.'
    }
    return
}

$vsCodePackageId = if ($installed.Source -match '\\Microsoft VS Code Insiders\\') {
    'Microsoft.VisualStudioCode.Insiders'
} elseif ($installed.Source -match '\\Microsoft VS Code\\') {
    'Microsoft.VisualStudioCode'
} else {
    $null
}

if ($null -ne $vsCodePackageId) {
    if (-not $wingetAvailable) {
        "- ripgrep: available through VS Code ($($installed.Display)). Update VS Code through its built-in updater to keep the bundled version current."
        return
    }

    $vsCodeIsManaged = Test-WingetPackageInstalled `
        -WingetCommand $WingetCommand `
        -PackageId $vsCodePackageId
    if ($vsCodeIsManaged -and
        (Test-WingetPackageUpdateAvailable -WingetCommand $WingetCommand -PackageId $vsCodePackageId)) {
        "- ripgrep: available through VS Code ($($installed.Display)); a VS Code update is available. Update: winget upgrade --id $vsCodePackageId --exact --source winget"
    }
    return
}

if (-not $wingetAvailable) {
    "- ripgrep: installed ($($installed.Display)), but the latest version could not be checked because winget is unavailable."
    return
}

$latest = Get-LatestWingetPackageVersion -WingetCommand $WingetCommand -PackageId $packageId
if ($null -eq $latest) {
    "- ripgrep: installed ($($installed.Display)), but the latest version check failed."
    return
}

if ($installed.Value -lt $latest.Value) {
    $managed = Test-WingetPackageInstalled -WingetCommand $WingetCommand -PackageId $packageId
    $command = if ($managed) {
        "winget upgrade --id $packageId --exact --source winget"
    } else {
        "winget install --id $packageId --exact --source winget"
    }
    "- ripgrep: update available ($($installed.Display) -> $($latest.Display)). Update: $command"
}
