$ErrorActionPreference = 'Stop'

$hookPath = Join-Path $PSScriptRoot 'check-target-session.ps1'

function Invoke-HookInput {
    param(
        [AllowEmptyString()]
        [string]$InputJson
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = (Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardInputEncoding = [System.Text.UTF8Encoding]::new($false)
    $startInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $startInfo.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    foreach ($argument in @(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $hookPath
    )) {
        $startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        $null = $process.Start()
        $outputTask = $process.StandardOutput.ReadToEndAsync()
        $errorTask = $process.StandardError.ReadToEndAsync()
        $process.StandardInput.Write($InputJson)
        $process.StandardInput.Close()
        $process.WaitForExit()
        [PSCustomObject]@{
            ExitCode = $process.ExitCode
            Output = $outputTask.GetAwaiter().GetResult()
            Error = $errorTask.GetAwaiter().GetResult()
        }
    } finally {
        $process.Dispose()
    }
}

$expectedSystemMessage = 'This session is using the VS Code Local harness. Open the Agents window, create a new session, and select Copilot as the Session Target to use this repository''s Copilot CLI-compatible hooks. Continue this task in that new session.'

$validCases = @(
    @{
        Name = 'Local harness sessionStart'
        Json = '{"hook_event_name":"SessionStart"}'
        ExpectMessage = $true
    },
    @{
        Name = 'Copilot CLI session payload (missing hook_event_name)'
        Json = '{"sessionId":"00000000-0000-0000-0000-000000000000"}'
        ExpectMessage = $false
    },
    @{
        Name = 'Different event name'
        Json = '{"hook_event_name":"UserPrompt"}'
        ExpectMessage = $false
    },
    @{
        Name = 'Case-sensitive event mismatch'
        Json = '{"hook_event_name":"sessionstart"}'
        ExpectMessage = $false
    }
)

$invalidCases = @(
    @{ Name = 'empty input'; Json = ''; Error = 'sessionStart hook input is empty\.' },
    @{ Name = 'whitespace input'; Json = " `t`r`n"; Error = 'sessionStart hook input is empty\.' },
    @{ Name = 'malformed JSON'; Json = '{'; Error = 'Failed to parse sessionStart hook input:' },
    @{ Name = 'null input'; Json = 'null'; Error = 'sessionStart hook input must be a JSON object\.' },
    @{ Name = 'empty array'; Json = '[]'; Error = 'sessionStart hook input must be a JSON object\.' },
    @{ Name = 'single-object array'; Json = '[{"hook_event_name":"SessionStart"}]'; Error = 'sessionStart hook input must be a JSON object\.' },
    @{ Name = 'multiple-object array'; Json = '[{"hook_event_name":"SessionStart"},{"hook_event_name":"SessionStart"}]'; Error = 'sessionStart hook input must be a JSON object\.' },
    @{ Name = 'string input'; Json = '"SessionStart"'; Error = 'sessionStart hook input must be a JSON object\.' },
    @{ Name = 'number input'; Json = '42'; Error = 'sessionStart hook input must be a JSON object\.' },
    @{ Name = 'boolean input'; Json = 'true'; Error = 'sessionStart hook input must be a JSON object\.' }
)

foreach ($case in $validCases) {
    $result = Invoke-HookInput -InputJson $case.Json
    if ($result.ExitCode -ne 0) {
        throw "Unexpected exit code for '$($case.Name)': got $($result.ExitCode), expected 0. stderr: $($result.Error)"
    }
    if ($result.Error) {
        throw "Unexpected stderr for '$($case.Name)': $($result.Error)"
    }

    if ($case.ExpectMessage) {
        try {
            $parsedOutput = $result.Output | ConvertFrom-Json
        } catch {
            throw "Failed to parse stdout as JSON for '$($case.Name)': $($result.Output)"
        }
        if ($parsedOutput.systemMessage -ne $expectedSystemMessage) {
            throw "Unexpected systemMessage for '$($case.Name)': got '$($parsedOutput.systemMessage)'"
        }
    } else {
        if (-not [string]::IsNullOrWhiteSpace($result.Output)) {
            throw "Expected empty output for '$($case.Name)', got: $($result.Output)"
        }
    }
}

foreach ($case in $invalidCases) {
    $result = Invoke-HookInput -InputJson $case.Json
    if ($result.ExitCode -ne 2) {
        throw "Unexpected exit code for '$($case.Name)': got $($result.ExitCode), expected 2. stdout: $($result.Output)"
    }
    if ($result.Output) {
        throw "Unexpected stdout for '$($case.Name)': $($result.Output)"
    }
    if ($result.Error -notmatch $case.Error) {
        throw "Unexpected error message for '$($case.Name)': got '$($result.Error)', expected pattern '$($case.Error)'"
    }
}

Write-Output "Passed $($validCases.Count + $invalidCases.Count) check-target-session tests."
