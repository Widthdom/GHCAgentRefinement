param(
    [Parameter(Mandatory)]
    [string]$TranscriptPath
)

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\common.ps1"

if (-not (Test-Path -LiteralPath $TranscriptPath -PathType Leaf)) {
    throw "Transcript file not found: $TranscriptPath"
}

$usedSkills = [System.Collections.Generic.List[string]]::new()

foreach ($line in [System.IO.File]::ReadLines($TranscriptPath)) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    $event = $line | ConvertFrom-Json

    if ($event.type -eq 'user.message' -and
        -not (Test-ResponseFooterCorrectionMessage -Content $event.data.content)) {
        $usedSkills.Clear()
        continue
    }

    if ($event.type -ne 'tool.execution_start' -or
        $event.data.toolName -notin @('skill', 'functions.skill')) {
        continue
    }

    $skillName = $event.data.arguments.skill
    if (-not [string]::IsNullOrWhiteSpace($skillName) -and
        -not $usedSkills.Contains($skillName)) {
        $usedSkills.Add($skillName)
    }
}

$usedSkills
