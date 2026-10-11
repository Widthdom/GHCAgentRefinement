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

$currentJst = & "$PSScriptRoot\helpers\add-response-footer\get-current-jst.ps1"
$usedHooks = @(& "$PSScriptRoot\helpers\add-response-footer\get-used-hooks.ps1" `
    -TranscriptPath $transcriptPath `
    -HooksPath $PSScriptRoot)
$usedSkills = @(& "$PSScriptRoot\helpers\add-response-footer\get-used-skills.ps1" -TranscriptPath $transcriptPath)
$expectedHooks = if ($usedHooks.Count -eq 0) { @('none') } else { $usedHooks }
$expectedSkills = if ($usedSkills.Count -eq 0) { @('none') } else { $usedSkills }
$requiredFooter = & "$PSScriptRoot\helpers\add-response-footer\format-response-footer.ps1" `
    -Hooks $expectedHooks `
    -Skills $expectedSkills `
    -CurrentJst $currentJst
$footerIsValid = & "$PSScriptRoot\helpers\add-response-footer\test-response-footer.ps1" `
    -Response $lastAssistantResponse `
    -ExpectedHooks $expectedHooks `
    -ExpectedSkills $expectedSkills `
    -CurrentJst $currentJst

if (-not $footerIsValid) {
    $output = @{
        decision = 'block'
        reason = @"
The final response footer is missing or incorrect. Continue the response with the following exact footer block only once:

$requiredFooter
"@
    } | ConvertTo-Json -Compress

    [Console]::Out.WriteLine($output)
}
