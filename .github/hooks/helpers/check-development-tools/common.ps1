$ErrorActionPreference = 'Stop'

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Command,

        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $ErrorActionPreference = 'Continue'
    $output = @(& $Command @Arguments 2>&1)
    [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output = @($output | ForEach-Object { $_.ToString() })
    }
}

function ConvertTo-Version {
    param(
        [Parameter(Mandatory)]
        [string]$Value
    )

    $match = [regex]::Match($Value, '\d+(?:\.\d+){1,3}')
    if (-not $match.Success) {
        return $null
    }

    try {
        return [version]$match.Value
    } catch {
        return $null
    }
}

function Get-ToolVersion {
    param(
        [Parameter(Mandatory)]
        [string]$Command,

        [Parameter(Mandatory)]
        [string[]]$Arguments,

        [Parameter(Mandatory)]
        [string]$Pattern
    )

    $commandInfo = Get-Command $Command -ErrorAction SilentlyContinue
    if ($null -eq $commandInfo) {
        return $null
    }

    $result = Invoke-NativeCommand -Command $Command -Arguments $Arguments
    if ($result.ExitCode -ne 0) {
        return $null
    }

    $match = [regex]::Match(($result.Output -join "`n"), $Pattern)
    if (-not $match.Success) {
        return $null
    }

    [pscustomobject]@{
        Display = $match.Value
        Match = $match
        Source = $commandInfo.Source
        Value = ConvertTo-Version -Value $match.Groups['version'].Value
    }
}

function Test-WingetAvailable {
    param(
        [Parameter(Mandatory)]
        [string]$Command
    )

    return $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

function Test-WingetOutputContainsPackage {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [string[]]$Output,

        [Parameter(Mandatory)]
        [string]$PackageId
    )

    $escapedPackageId = [regex]::Escape($PackageId)
    $matchingRows = @($Output | Where-Object {
        $_ -match "(?:^|\s)$escapedPackageId(?:\s|$)"
    })
    if ($matchingRows.Count -eq 0) {
        return $false
    }

    return $true
}

function Get-LatestWingetPackageVersion {
    param(
        [Parameter(Mandatory)]
        [string]$WingetCommand,

        [Parameter(Mandatory)]
        [string]$PackageId
    )

    $result = Invoke-NativeCommand -Command $WingetCommand -Arguments @(
        'show',
        '--id', $PackageId,
        '--exact',
        '--source', 'winget',
        '--versions',
        '--accept-source-agreements',
        '--disable-interactivity'
    )
    if ($result.ExitCode -ne 0) {
        return $null
    }

    foreach ($line in $result.Output) {
        $candidate = $line.Trim()
        if ($candidate -notmatch '^\d+(?:\.\d+){1,3}$') {
            continue
        }

        return [pscustomobject]@{
            Display = $candidate
            Value = ConvertTo-Version -Value $candidate
        }
    }

    return $null
}

function Test-WingetPackageInstalled {
    param(
        [Parameter(Mandatory)]
        [string]$WingetCommand,

        [Parameter(Mandatory)]
        [string]$PackageId
    )

    $result = Invoke-NativeCommand -Command $WingetCommand -Arguments @(
        'list',
        '--id', $PackageId,
        '--exact',
        '--source', 'winget',
        '--accept-source-agreements',
        '--disable-interactivity'
    )
    if ($result.ExitCode -ne 0) {
        return $false
    }

    return Test-WingetOutputContainsPackage -Output $result.Output -PackageId $PackageId
}

function Test-WingetPackageUpdateAvailable {
    param(
        [Parameter(Mandatory)]
        [string]$WingetCommand,

        [Parameter(Mandatory)]
        [string]$PackageId
    )

    $result = Invoke-NativeCommand -Command $WingetCommand -Arguments @(
        'list',
        '--id', $PackageId,
        '--exact',
        '--upgrade-available',
        '--source', 'winget',
        '--accept-source-agreements',
        '--disable-interactivity'
    )
    if ($result.ExitCode -ne 0) {
        return $false
    }

    return Test-WingetOutputContainsPackage -Output $result.Output -PackageId $PackageId
}
