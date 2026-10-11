$ErrorActionPreference = 'Stop'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$inputJson = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($inputJson)) {
    [Console]::Error.WriteLine('agentStop hook input is empty.')
    exit 2
}

try {
    $hookInput = $inputJson | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine("Failed to parse agentStop hook input: $($_.Exception.Message)")
    exit 2
}

$transcriptPath = if ($hookInput.transcriptPath) {
    $hookInput.transcriptPath
} else {
    $hookInput.transcript_path
}

if ([string]::IsNullOrWhiteSpace($transcriptPath) -or
    -not (Test-Path -LiteralPath $transcriptPath -PathType Leaf)) {
    [Console]::Error.WriteLine("Transcript file not found: $transcriptPath")
    exit 2
}

$lastAssistantResponse = $null
foreach ($line in [System.IO.File]::ReadLines($transcriptPath)) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    try {
        $event = $line | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine("Failed to parse transcript event: $($_.Exception.Message)")
        exit 2
    }

    if ($event.type -eq 'assistant.message' -and
        $event.data.content -is [string] -and
        -not [string]::IsNullOrWhiteSpace($event.data.content)) {
        $lastAssistantResponse = $event.data.content
    }
}

if ([string]::IsNullOrWhiteSpace($lastAssistantResponse)) {
    [Console]::Error.WriteLine('No assistant response was found in the transcript.')
    exit 2
}

$currentJst = & "$PSScriptRoot\scripts\add-response-footer\get-current-jst.ps1"
$usedSkills = @(& "$PSScriptRoot\scripts\add-response-footer\get-used-skills.ps1" -TranscriptPath $transcriptPath)
$expectedSkills = if ($usedSkills.Count -eq 0) { @('none') } else { $usedSkills }
$requiredFooter = & "$PSScriptRoot\scripts\add-response-footer\format-response-footer.ps1" `
    -Skills $expectedSkills `
    -CurrentJst $currentJst
$footerIsValid = & "$PSScriptRoot\scripts\add-response-footer\test-response-footer.ps1" `
    -Response $lastAssistantResponse `
    -ExpectedSkills $expectedSkills `
    -CurrentJst $currentJst

if (-not $footerIsValid) {
    $output = @{
        decision = 'block'
        reason = @"
The final response footer is missing or incorrect. Append the following exact footer as the final content:

$requiredFooter
"@
    } | ConvertTo-Json -Compress

    [Console]::Out.WriteLine($output)
}
