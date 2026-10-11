param(
    [Parameter(Mandatory)]
    [string]$TranscriptPath,

    [Parameter(Mandatory)]
    [string]$HooksPath
)

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\common.ps1"

if (-not (Test-Path -LiteralPath $TranscriptPath -PathType Leaf)) {
    throw "Transcript file not found: $TranscriptPath"
}

if (-not (Test-Path -LiteralPath $HooksPath -PathType Container)) {
    throw "Hooks directory not found: $HooksPath"
}

$hookTypes = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)

foreach ($line in [System.IO.File]::ReadLines($TranscriptPath)) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    $event = $line | ConvertFrom-Json

    if ($event.type -eq 'user.message' -and
        -not (Test-ResponseFooterCorrectionMessage -Content $event.data.content)) {
        $hookTypes.Clear()
        continue
    }

    if ($event.type -eq 'hook.start' -and
        -not [string]::IsNullOrWhiteSpace($event.data.hookType)) {
        $null = $hookTypes.Add($event.data.hookType)
    }
}

$usedHooks = [System.Collections.Generic.List[string]]::new()
foreach ($hookFile in Get-ChildItem -LiteralPath $HooksPath -Filter '*.json' -File | Sort-Object Name) {
    $hookDefinition = [System.IO.File]::ReadAllText($hookFile.FullName) | ConvertFrom-Json
    $configuredHookTypes = @($hookDefinition.hooks.PSObject.Properties.Name)

    if ($configuredHookTypes | Where-Object { $hookTypes.Contains($_) }) {
        $usedHooks.Add($hookFile.BaseName)
    }
}

$usedHooks
