$ErrorActionPreference = 'Stop'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$inputJson = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($inputJson)) {
    [Console]::Error.WriteLine('userPromptSubmitted hook input is empty.')
    exit 2
}

try {
    $hookInput = $inputJson | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine("Failed to parse userPromptSubmitted hook input: $($_.Exception.Message)")
    exit 2
}

$sessionId = if ($hookInput.sessionId) {
    $hookInput.sessionId
} else {
    $hookInput.session_id
}
$prompt = [string]$hookInput.prompt
if ([string]::IsNullOrWhiteSpace($prompt)) {
    [Console]::Error.WriteLine('userPromptSubmitted hook input does not contain a prompt.')
    exit 2
}

if ($prompt.StartsWith(
        'The final response footer is missing or incorrect.',
        [System.StringComparison]::Ordinal
    )) {
    exit 0
}

$parsedSessionId = [guid]::Empty
if (-not [guid]::TryParse($sessionId, [ref]$parsedSessionId)) {
    [Console]::Error.WriteLine("Invalid session ID: $sessionId")
    exit 2
}

$stateDirectory = Join-Path ([System.IO.Path]::GetTempPath()) 'ghc-agent-refinement\debug-mode'
[System.IO.Directory]::CreateDirectory($stateDirectory) | Out-Null
$statePath = Join-Path $stateDirectory "$($parsedSessionId.ToString('D')).json"
$state = @{
    enabled = $prompt.StartsWith('??', [System.StringComparison]::Ordinal)
    updatedAt = [DateTimeOffset]::UtcNow.ToString('O')
} | ConvertTo-Json -Compress

[System.IO.File]::WriteAllText(
    $statePath,
    $state,
    [System.Text.UTF8Encoding]::new($false)
)
