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

function Get-InstalledPython {
    if ($resolvedPlatform -eq 'Windows') {
        $installed = Get-ToolVersion `
            -Command 'py' `
            -Arguments @('-3', '--version') `
            -Pattern 'Python (?<version>\d+(?:\.\d+){2,3})'
        if ($null -ne $installed) {
            return $installed
        }
    }

    $command = if ($resolvedPlatform -eq 'Windows') { 'python' } else { 'python3' }
    Get-ToolVersion `
        -Command $command `
        -Arguments @('--version') `
        -Pattern 'Python (?<version>\d+(?:\.\d+){2,3})'
}

function Get-LatestPythonPackage {
    $result = Invoke-NativeCommand -Command $WingetCommand -Arguments @(
        'search',
        '--id', 'Python.Python',
        '--source', 'winget',
        '--accept-source-agreements',
        '--disable-interactivity'
    )
    if ($result.ExitCode -ne 0) {
        return $null
    }

    $packages = foreach ($line in $result.Output) {
        $match = [regex]::Match(
            $line,
            'Python\.Python\.(?<major>\d+)\.(?<minor>\d+)\s+(?<version>\d+(?:\.\d+){2,3})\s*$'
        )
        if (-not $match.Success -or [int]$match.Groups['major'].Value -lt 3) {
            continue
        }

        [pscustomobject]@{
            Id = "Python.Python.$($match.Groups['major'].Value).$($match.Groups['minor'].Value)"
            Display = $match.Groups['version'].Value
            Value = ConvertTo-Version -Value $match.Groups['version'].Value
        }
    }

    $packages |
        Sort-Object -Property Value -Descending |
        Select-Object -First 1
}

$resolvedPlatform = Get-DevelopmentPlatform -Platform $Platform
$packageManager = Get-DevelopmentPackageManager @PSBoundParameters
$installed = Get-InstalledPython
$packageId = switch ($resolvedPlatform) {
    'MacOS' { 'python' }
    'Linux' { 'python3' }
}

if ($null -eq $packageManager) {
    if ($null -eq $installed) {
        "- Python: not installed. No supported package manager is available for $resolvedPlatform, so no install command can be suggested."
    } else {
        "- Python: installed ($($installed.Display)), but the latest version could not be checked because no supported package manager is available for $resolvedPlatform."
    }
    return
}

$latest = if ($resolvedPlatform -eq 'Windows') {
    Get-LatestPythonPackage
} else {
    Get-LatestPackageVersion -PackageManager $packageManager -PackageId $packageId
}
if ($null -eq $installed) {
    if ($null -eq $latest) {
        '- Python: not installed, and the latest stable package could not be found.'
    } else {
        $latestPackageId = if ($resolvedPlatform -eq 'Windows') { $latest.Id } else { $packageId }
        $command = Get-PackageInstallCommand `
            -PackageManager $packageManager `
            -PackageId $latestPackageId
        "- Python: not installed. Install: $command"
    }
    return
}

if ($null -eq $latest) {
    "- Python: installed ($($installed.Display)), but the latest version check failed."
    return
}

if ($installed.Value -lt $latest.Value) {
    $latestPackageId = if ($resolvedPlatform -eq 'Windows') {
        $latest.Id
    } else {
        $packageId
    }
    $managed = Test-PackageManaged `
        -PackageManager $packageManager `
        -PackageId $latestPackageId
    $sameSeries = $installed.Value.Major -eq $latest.Value.Major -and
        $installed.Value.Minor -eq $latest.Value.Minor
    $command = Get-PackageInstallCommand `
        -PackageManager $packageManager `
        -PackageId $latestPackageId `
        -Upgrade:($managed -and ($resolvedPlatform -ne 'Windows' -or $sameSeries))
    "- Python: update available ($($installed.Display) -> $($latest.Display)). Update: $command"
}
