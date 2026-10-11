param(
    [string]$WingetCommand = 'winget'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"

function Get-InstalledPython {
    $installed = Get-ToolVersion `
        -Command 'py' `
        -Arguments @('-3', '--version') `
        -Pattern 'Python (?<version>\d+(?:\.\d+){2,3})'
    if ($null -ne $installed) {
        return $installed
    }

    Get-ToolVersion `
        -Command 'python' `
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

$installed = Get-InstalledPython
$wingetAvailable = Test-WingetAvailable -Command $WingetCommand

if (-not $wingetAvailable) {
    if ($null -eq $installed) {
        '- Python: not installed. winget is unavailable, so no install command can be suggested.'
    } else {
        "- Python: installed ($($installed.Display)), but the latest version could not be checked because winget is unavailable."
    }
    return
}

$latest = Get-LatestPythonPackage
if ($null -eq $installed) {
    if ($null -eq $latest) {
        '- Python: not installed, and the latest stable winget package could not be found.'
    } else {
        "- Python: not installed. Install: winget install --id $($latest.Id) --exact --source winget"
    }
    return
}

if ($null -eq $latest) {
    "- Python: installed ($($installed.Display)), but the latest version check failed."
    return
}

if ($installed.Value -lt $latest.Value) {
    $sameSeries = $installed.Value.Major -eq $latest.Value.Major -and
        $installed.Value.Minor -eq $latest.Value.Minor
    $managed = Test-WingetPackageInstalled -WingetCommand $WingetCommand -PackageId $latest.Id
    $command = if ($sameSeries -and $managed) {
        "winget upgrade --id $($latest.Id) --exact --source winget"
    } else {
        "winget install --id $($latest.Id) --exact --source winget"
    }
    "- Python: update available ($($installed.Display) -> $($latest.Display)). Update: $command"
}
