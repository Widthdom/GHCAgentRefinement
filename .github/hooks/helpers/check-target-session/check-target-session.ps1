$ErrorActionPreference = 'Stop'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$inputJson = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($inputJson)) {
    [Console]::Error.WriteLine('sessionStart hook input is empty.')
    exit 2
}

try {
    $hookInput = $inputJson | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine("Failed to parse sessionStart hook input: $($_.Exception.Message)")
    exit 2
}

if ($hookInput.hook_event_name -cne 'SessionStart') {
    exit 0
}

@{
    systemMessage = 'This session is using the VS Code Local harness. Open the Agents window, create a new session, and select Copilot as the Session Target to use this repository''s Copilot CLI-compatible hooks. Continue this task in that new session.'
} | ConvertTo-Json -Compress
