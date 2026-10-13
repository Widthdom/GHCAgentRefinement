$ErrorActionPreference = 'Stop'

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Command,

        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $ErrorActionPreference = 'Continue'
    $LASTEXITCODE = $null
    $output = @(& $Command @Arguments 2>&1)
    $succeeded = $?
    [pscustomobject]@{
        ExitCode = if ($null -eq $LASTEXITCODE) {
            if ($succeeded) { 0 } else { 1 }
        } else {
            $LASTEXITCODE
        }
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
        $version = [version]$match.Value
        return [version]::new(
            $version.Major,
            $version.Minor,
            [Math]::Max(0, $version.Build),
            [Math]::Max(0, $version.Revision)
        )
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

function Get-DevelopmentPlatform {
    param(
        [ValidateSet('Auto', 'Windows', 'MacOS', 'Linux')]
        [string]$Platform = 'Auto'
    )

    if ($Platform -ne 'Auto') {
        return $Platform
    }

    if ($IsWindows) {
        return 'Windows'
    }
    if ($IsMacOS) {
        return 'MacOS'
    }
    if ($IsLinux) {
        return 'Linux'
    }

    throw 'Unsupported operating system.'
}

function Get-DevelopmentPackageManager {
    param(
        [ValidateSet('Auto', 'Windows', 'MacOS', 'Linux')]
        [string]$Platform = 'Auto',

        [string]$WingetCommand = 'winget',

        [string]$BrewCommand = 'brew',

        [string]$AptCommand = 'apt-cache',

        [string]$DnfCommand = 'dnf',

        [string]$PacmanCommand = 'pacman'
    )

    $resolvedPlatform = Get-DevelopmentPlatform -Platform $Platform
    $candidates = switch ($resolvedPlatform) {
        'Windows' {
            @([pscustomobject]@{ Kind = 'winget'; Command = $WingetCommand })
        }
        'MacOS' {
            @([pscustomobject]@{ Kind = 'brew'; Command = $BrewCommand })
        }
        'Linux' {
            @(
                [pscustomobject]@{ Kind = 'apt'; Command = $AptCommand }
                [pscustomobject]@{ Kind = 'dnf'; Command = $DnfCommand }
                [pscustomobject]@{ Kind = 'pacman'; Command = $PacmanCommand }
            )
        }
    }

    foreach ($candidate in $candidates) {
        if ($null -ne (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) {
            return $candidate
        }
    }

    return $null
}

function Get-LatestPackageVersion {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$PackageManager,

        [Parameter(Mandatory)]
        [string]$PackageId
    )

    if ($PackageManager.Kind -eq 'winget') {
        return Get-LatestWingetPackageVersion `
            -WingetCommand $PackageManager.Command `
            -PackageId $PackageId
    }

    $arguments = switch ($PackageManager.Kind) {
        'brew' { @('info', '--json=v2', $PackageId) }
        'apt' { @('policy', $PackageId) }
        'dnf' { @('--quiet', 'info', $PackageId) }
        'pacman' { @('-Si', $PackageId) }
    }
    $result = Invoke-NativeCommand -Command $PackageManager.Command -Arguments $arguments
    if ($result.ExitCode -ne 0) {
        return $null
    }

    $display = switch ($PackageManager.Kind) {
        'brew' {
            try {
                $data = $result.Output -join "`n" | ConvertFrom-Json
                $data.formulae[0].versions.stable
            } catch {
                $null
            }
        }
        'apt' {
            $match = [regex]::Match(
                ($result.Output -join "`n"),
                '(?m)^\s*Candidate:\s*(?<version>\S+)'
            )
            if ($match.Success -and $match.Groups['version'].Value -ne '(none)') {
                $match.Groups['version'].Value
            }
        }
        'dnf' {
            $matches = [regex]::Matches(
                ($result.Output -join "`n"),
                '(?m)^\s*Version\s*:\s*(?<version>\S+)'
            )
            $versions = @($matches | ForEach-Object {
                $_.Groups['version'].Value
            })
            $versions |
                Sort-Object -Property { ConvertTo-Version -Value $_ } -Descending |
                Select-Object -First 1
        }
        'pacman' {
            $match = [regex]::Match(
                ($result.Output -join "`n"),
                '(?m)^\s*Version\s*:\s*(?<version>\S+)'
            )
            if ($match.Success) {
                $match.Groups['version'].Value
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($display)) {
        return $null
    }

    [pscustomobject]@{
        Display = $display
        Value = ConvertTo-Version -Value $display
    }
}

function Test-PackageManaged {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$PackageManager,

        [Parameter(Mandatory)]
        [string]$PackageId
    )

    if ($PackageManager.Kind -eq 'winget') {
        return Test-WingetPackageInstalled `
            -WingetCommand $PackageManager.Command `
            -PackageId $PackageId
    }

    $command = $PackageManager.Command
    $arguments = switch ($PackageManager.Kind) {
        'brew' { @('list', '--versions', $PackageId) }
        'apt' {
            $command = 'dpkg-query'
            @('-W', '-f=${Status}', $PackageId)
        }
        'dnf' {
            $command = 'rpm'
            @('-q', $PackageId)
        }
        'pacman' { @('-Q', $PackageId) }
    }
    if ($null -eq (Get-Command $command -ErrorAction SilentlyContinue)) {
        return $false
    }

    $result = Invoke-NativeCommand -Command $command -Arguments $arguments
    return $result.ExitCode -eq 0
}

function Get-PackageInstallCommand {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$PackageManager,

        [Parameter(Mandatory)]
        [string]$PackageId,

        [switch]$Upgrade
    )

    switch ($PackageManager.Kind) {
        'winget' {
            $action = if ($Upgrade) { 'upgrade' } else { 'install' }
            "winget $action --id $PackageId --exact --source winget"
        }
        'brew' {
            $action = if ($Upgrade) { 'upgrade' } else { 'install' }
            "brew $action $PackageId"
        }
        'apt' {
            if ($Upgrade) {
                "sudo apt-get install --only-upgrade $PackageId"
            } else {
                "sudo apt-get install $PackageId"
            }
        }
        'dnf' {
            $action = if ($Upgrade) { 'upgrade' } else { 'install' }
            "sudo dnf $action $PackageId"
        }
        'pacman' {
            "sudo pacman -S $PackageId"
        }
    }
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
