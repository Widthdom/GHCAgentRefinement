param(
    [ValidateSet('Auto', 'Windows', 'MacOS', 'Linux')]
    [string]$Platform = 'Auto',
    [string]$WingetCommand = 'winget',
    [string]$BrewCommand = 'brew',
    [string]$AptCommand = 'apt-cache',
    [string]$DnfCommand = 'dnf',
    [string]$PacmanCommand = 'pacman',
    # テストでPowerShellのバージョンを差し替えられるようにする。
    [string]$PowerShellCommand = 'pwsh'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

function Get-LatestPowerShellVersion {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$PackageManager,

        [Parameter(Mandatory)]
        [string]$PackageId
    )

    if ($PackageManager.Kind -ne 'brew') {
        return Get-LatestPackageVersion `
            -PackageManager $PackageManager `
            -PackageId $PackageId
    }

    $result = Invoke-NativeCommand -Command $PackageManager.Command -Arguments @(
        'info', '--json=v2', '--cask', $PackageId
    )
    if ($result.ExitCode -ne 0) {
        return $null
    }

    try {
        $data = $result.Output -join "`n" | ConvertFrom-Json
        $display = $data.casks[0].version
        if ([string]::IsNullOrWhiteSpace($display)) {
            return $null
        }

        $value = ConvertTo-Version -Value $display
        if ($null -eq $value) {
            return $null
        }

        [pscustomobject]@{
            Display = $display
            Value = $value
        }
    } catch {
        return $null
    }
}

function Test-PowerShellPackageManaged {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$PackageManager,

        [Parameter(Mandatory)]
        [string]$PackageId
    )

    if ($PackageManager.Kind -ne 'brew') {
        return Test-PackageManaged `
            -PackageManager $PackageManager `
            -PackageId $PackageId
    }

    $result = Invoke-NativeCommand -Command $PackageManager.Command -Arguments @(
        'list', '--cask', $PackageId
    )
    return $result.ExitCode -eq 0
}

function Get-PowerShellPackageCommand {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$PackageManager,

        [Parameter(Mandatory)]
        [string]$PackageId,

        [switch]$Upgrade
    )

    if ($PackageManager.Kind -eq 'brew') {
        $action = if ($Upgrade) { 'upgrade' } else { 'install' }
        return "brew $action --cask $PackageId"
    }

    return Get-PackageInstallCommand `
        -PackageManager $PackageManager `
        -PackageId $PackageId `
        -Upgrade:$Upgrade
}

$resolvedPlatform = Get-DevelopmentPlatform -Platform $Platform
$packageManager = Get-DevelopmentPackageManager `
    -Platform $Platform `
    -WingetCommand $WingetCommand `
    -BrewCommand $BrewCommand `
    -AptCommand $AptCommand `
    -DnfCommand $DnfCommand `
    -PacmanCommand $PacmanCommand
$packageId = if ($resolvedPlatform -eq 'Windows') {
    'Microsoft.PowerShell'
} else {
    'powershell'
}
$installed = Get-ToolVersion `
    -Command $PowerShellCommand `
    -Arguments @('-NoLogo', '-NoProfile', '-NonInteractive', '-Command', '$PSVersionTable.PSVersion.ToString()') `
    -Pattern '(?<version>\d+(?:\.\d+){1,3})'

if ($null -eq $installed) {
    if ($null -ne $packageManager) {
        $command = Get-PowerShellPackageCommand `
            -PackageManager $packageManager `
            -PackageId $packageId
        "- PowerShell: not installed. Install: $command"
    } else {
        "- PowerShell: not installed. No supported package manager is available for $resolvedPlatform, so no install command can be suggested."
    }
    return
}

if ($installed.Value.Major -lt 7) {
    '- PowerShell: PowerShell 7 or later is required; the available pwsh command does not meet this requirement.'
    return
}

if ($null -eq $packageManager) {
    "- PowerShell: installed ($($installed.Display)), but the latest version could not be checked because no supported package manager is available for $resolvedPlatform."
    return
}

$latest = Get-LatestPowerShellVersion `
    -PackageManager $packageManager `
    -PackageId $packageId
if ($null -eq $latest -or $latest.Value.Major -lt 7) {
    "- PowerShell: installed ($($installed.Display)), but the latest PowerShell 7 or later version could not be determined."
    return
}

if ($installed.Value -lt $latest.Value) {
    $managed = Test-PowerShellPackageManaged `
        -PackageManager $packageManager `
        -PackageId $packageId
    $command = Get-PowerShellPackageCommand `
        -PackageManager $packageManager `
        -PackageId $packageId `
        -Upgrade:$managed
    "- PowerShell: update available ($($installed.Display) -> $($latest.Display)). Update: $command"
}
