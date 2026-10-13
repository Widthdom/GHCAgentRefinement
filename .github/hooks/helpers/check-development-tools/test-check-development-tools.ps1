$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'common.ps1')

$hookPath = Join-Path $PSScriptRoot 'check-development-tools.ps1'
$temporaryRoot = Join-Path (Join-Path (Join-Path `
    ([System.IO.Path]::GetTempPath()) 'ghc-agent-refinement') 'development-tools') `
    ([guid]::NewGuid().ToString('D'))
New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null

function New-FakePackageManager {
    param(
        [Parameter(Mandatory)]
        [string]$Name,

        [Parameter(Mandatory)]
        [string]$Output
    )

    $path = Join-Path $temporaryRoot "$Name.ps1"
    @"
`$OutputEncoding = [System.Text.UTF8Encoding]::new(`$false)
@'
$Output
'@ -split '\r?\n'
"@ | Set-Content -LiteralPath $path -Encoding UTF8
    return $path
}

function New-FakePowerShell {
    param(
        [Parameter(Mandatory)]
        [string]$Version
    )

    $path = Join-Path $temporaryRoot "pwsh-$Version.ps1"
    @"
Write-Output '$Version'
"@ | Set-Content -LiteralPath $path -Encoding UTF8
    return $path
}

function Assert-Equal {
    param(
        [Parameter(Mandatory)]
        [AllowNull()]
        $Actual,

        [Parameter(Mandatory)]
        [AllowNull()]
        $Expected,

        [Parameter(Mandatory)]
        [string]$Message
    )

    if ($Actual -ne $Expected) {
        throw "$Message Expected '$Expected', got '$Actual'."
    }
}

try {
    $versionCases = @(
        @{ Input = '7.6'; Expected = '7.6.0.0' }
        @{ Input = '7.6.0'; Expected = '7.6.0.0' }
        @{ Input = '7.6.6'; Expected = '7.6.6.0' }
        @{ Input = '7.6.6.0'; Expected = '7.6.6.0' }
        @{ Input = '7.6.6.1'; Expected = '7.6.6.1' }
    )
    foreach ($case in $versionCases) {
        Assert-Equal `
            -Actual (ConvertTo-Version -Value $case.Input).ToString() `
            -Expected $case.Expected `
            -Message 'Version normalization was incorrect.'
    }

    $brew = New-FakePackageManager -Name 'brew' -Output @'
{"formulae":[{"versions":{"stable":"15.2.0"}}]}
'@
    $apt = New-FakePackageManager -Name 'apt-cache' -Output @'
ripgrep:
  Installed: 14.1.0-1
  Candidate: 15.1.0-1
'@
    $dnf = New-FakePackageManager -Name 'dnf' -Output @'
Name         : ripgrep
Version      : 14.1.1
Name         : ripgrep
Version      : 15.0.0
'@
    $pacman = New-FakePackageManager -Name 'pacman' -Output @'
Repository      : extra
Name            : ripgrep
Version         : 15.1.0-1
'@

    $cases = @(
        @{ Kind = 'brew'; Command = $brew; Expected = '15.2.0' }
        @{ Kind = 'apt'; Command = $apt; Expected = '15.1.0' }
        @{ Kind = 'dnf'; Command = $dnf; Expected = '15.0.0' }
        @{ Kind = 'pacman'; Command = $pacman; Expected = '15.1.0' }
    )
    foreach ($case in $cases) {
        $manager = [pscustomobject]@{
            Kind = $case.Kind
            Command = $case.Command
        }
        $latest = Get-LatestPackageVersion `
            -PackageManager $manager `
            -PackageId 'ripgrep'
        if ($null -eq $latest -or $null -eq $latest.Value) {
            throw "Failed to parse the $($case.Kind) package version."
        }
        Assert-Equal `
            -Actual $latest.Value `
            -Expected (ConvertTo-Version -Value $case.Expected) `
            -Message "Failed to parse the $($case.Kind) package version."
    }

    $linuxManager = Get-DevelopmentPackageManager `
        -Platform Linux `
        -AptCommand $apt `
        -DnfCommand $dnf `
        -PacmanCommand $pacman
    Assert-Equal `
        -Actual $linuxManager.Kind `
        -Expected 'apt' `
        -Message 'Linux package manager precedence was incorrect.'

    $commands = @{
        winget = 'winget upgrade --id Git.Git --exact --source winget'
        brew = 'brew upgrade git'
        apt = 'sudo apt-get install --only-upgrade git'
        dnf = 'sudo dnf upgrade git'
        pacman = 'sudo pacman -S git'
    }
    foreach ($kind in $commands.Keys) {
        $manager = [pscustomobject]@{ Kind = $kind; Command = $kind }
        $packageId = if ($kind -eq 'winget') { 'Git.Git' } else { 'git' }
        $command = Get-PackageInstallCommand `
            -PackageManager $manager `
            -PackageId $packageId `
            -Upgrade
        Assert-Equal `
            -Actual $command `
            -Expected $commands[$kind] `
            -Message "The $kind update command was incorrect."
    }

    $brewCask = New-FakePackageManager -Name 'brew-cask' -Output @'
{"casks":[{"token":"powershell","version":"99.0.0"}]}
'@
    $oldPowerShell = New-FakePowerShell -Version '5.1.0'
    $powershellOutput = & (Join-Path $PSScriptRoot 'check-powershell.ps1') `
        -Platform MacOS `
        -BrewCommand $brewCask `
        -PowerShellCommand $oldPowerShell
    if ($powershellOutput -notmatch
        'PowerShell: PowerShell 7 or later is required; the available pwsh command does not meet this requirement\.') {
        throw "The PowerShell minimum-version warning was incorrect: $powershellOutput"
    }
    if ($powershellOutput -match '5\.1|99\.0\.0|Recommended:|Update:') {
        throw "The checker used or recommended an unsupported PowerShell version: $powershellOutput"
    }

    $newPowerShell = New-FakePowerShell -Version '7.5.0'
    $installedPowerShell = New-FakePowerShell -Version '7.6.6'
    $powerShellCases = @(
        @{ Latest = '7.6.6'; Update = $false }
        @{ Latest = '7.6.6.0'; Update = $false }
        @{ Latest = '7.6.5.0'; Update = $false }
        @{ Latest = '7.6.6.1'; Update = $true }
        @{ Latest = '7.6.7.0'; Update = $true }
    )
    foreach ($case in $powerShellCases) {
        $winget = New-FakePackageManager -Name "winget-$($case.Latest)" -Output @"
Microsoft.PowerShell
$($case.Latest)
"@
        $output = @(& (Join-Path $PSScriptRoot 'check-powershell.ps1') `
            -Platform Windows `
            -WingetCommand $winget `
            -PowerShellCommand $installedPowerShell)
        $expectedOutput = if ($case.Update) {
            "- PowerShell: update available (7.6.6 -> $($case.Latest)). Update: winget upgrade --id Microsoft.PowerShell --exact --source winget"
        } else {
            ''
        }
        Assert-Equal `
            -Actual ($output -join "`n") `
            -Expected $expectedOutput `
            -Message "The PowerShell update check for $($case.Latest) was incorrect."
    }

    $oldCask = New-FakePackageManager -Name 'brew-cask-old' -Output @'
{"casks":[{"token":"powershell","version":"5.1.0"}]}
'@
    $unsupportedLatestOutput = & (Join-Path $PSScriptRoot 'check-powershell.ps1') `
        -Platform MacOS `
        -BrewCommand $oldCask `
        -PowerShellCommand $newPowerShell
    if ($unsupportedLatestOutput -notmatch
        'latest PowerShell 7 or later version could not be determined') {
        throw "The checker did not reject an unsupported latest version: $unsupportedLatestOutput"
    }
    if ($unsupportedLatestOutput -match '5\.1|Update:') {
        throw "The checker exposed an unsupported PowerShell version: $unsupportedLatestOutput"
    }

    $hookOutput = & $hookPath `
        -Platform Linux `
        -AptCommand $apt `
        -DnfCommand (Join-Path $temporaryRoot 'missing-dnf') `
        -PacmanCommand (Join-Path $temporaryRoot 'missing-pacman')
    $hookResult = $hookOutput | ConvertFrom-Json
    if ($hookResult.additionalContext -notmatch
        'The Linux development-tool check found items that need attention:') {
        throw 'The hook did not report the selected Linux platform.'
    }
    if ($hookResult.additionalContext -notmatch 'sudo apt-get install') {
        throw 'The hook did not suggest an apt command on Linux.'
    }
    if ($hookResult.additionalContext -notmatch 'PowerShell: update available .*apt-get install powershell') {
        throw 'The hook did not include the PowerShell update recommendation.'
    }

    Write-Output "Passed $($versionCases.Count + $powerShellCases.Count + $cases.Count + $commands.Count + 5) development-tool tests."
} finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}
