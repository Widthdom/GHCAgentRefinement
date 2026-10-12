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

$sessionId = if ($hookInput.sessionId) {
    $hookInput.sessionId
} else {
    $hookInput.session_id
}
$parsedSessionId = [guid]::Empty
if (-not [guid]::TryParse($sessionId, [ref]$parsedSessionId)) {
    [Console]::Error.WriteLine("Invalid session ID: $sessionId")
    exit 2
}

$statePath = Join-Path (Join-Path (Join-Path `
    ([System.IO.Path]::GetTempPath()) 'ghc-agent-refinement') 'debug-mode') `
    "$($parsedSessionId.ToString('D')).json"
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    exit 0
}

try {
    $debugState = [System.IO.File]::ReadAllText($statePath) | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine("Failed to parse footer debug-mode state: $($_.Exception.Message)")
    exit 2
}

if ($debugState.enabled -ne $true) {
    exit 0
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

$currentJst = & (Join-Path $PSScriptRoot 'get-current-jst.ps1')
$usedHooks = @(& (Join-Path $PSScriptRoot 'get-used-hooks.ps1') `
    -TranscriptPath $transcriptPath `
    -HooksPath (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
$usedSkills = @(& (Join-Path $PSScriptRoot 'get-used-skills.ps1') -TranscriptPath $transcriptPath)
$expectedHooks = if ($usedHooks.Count -eq 0) { @('none') } else { $usedHooks }
$expectedSkills = if ($usedSkills.Count -eq 0) { @('none') } else { $usedSkills }
$requiredFooter = & (Join-Path $PSScriptRoot 'format-response-footer.ps1') `
    -Hooks $expectedHooks `
    -Skills $expectedSkills `
    -CurrentJst $currentJst
$footerIsValid = & (Join-Path $PSScriptRoot 'test-response-footer.ps1') `
    -Response $lastAssistantResponse `
    -ExpectedHooks $expectedHooks `
    -ExpectedSkills $expectedSkills `
    -CurrentJst $currentJst

if (-not $footerIsValid) {
    $output = @{
        decision = 'block'
        reason = @"
The final response footer is missing or incorrect. Preserve the response body. Replace any existing footer blocks with the exact block below; if no footer exists, append it to the end. The corrected response must contain exactly one footer block:

$requiredFooter
"@
    } | ConvertTo-Json -Compress

    [Console]::Out.WriteLine($output)
}
