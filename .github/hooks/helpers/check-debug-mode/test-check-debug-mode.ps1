$ErrorActionPreference = 'Stop'

$hookPath = Join-Path $PSScriptRoot 'check-debug-mode.ps1'
$sessionId = [System.Guid]::NewGuid().ToString('D')
$statePath = Join-Path (Join-Path (Join-Path `
    ([System.IO.Path]::GetTempPath()) 'ghc-agent-refinement') 'debug-mode') `
    "$sessionId.json"

function Invoke-HookInput {
    param(
        [Parameter(Mandatory)]
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
    foreach ($argument in @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $hookPath)) {
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

$validCases = @(
    @{ Name = 'regular prompt'; Prompt = 'regular prompt'; Enabled = $false },
    @{ Name = 'debug prompt'; Prompt = '?? debug prompt'; Enabled = $true },
    @{ Name = 'next regular prompt'; Prompt = 'next regular user prompt'; Enabled = $false },
    @{ Name = 'leading whitespace'; Prompt = ' ?? debug prompt'; Enabled = $false },
    @{ Name = 'embedded marker'; Prompt = 'regular ?? prompt'; Enabled = $false },
    @{ Name = 'marker only'; Prompt = '??'; Enabled = $true },
    @{ Name = 'Unicode prompt and normalized GUID'; Prompt = '?? 日本語'; Enabled = $true;
       SessionId = ([System.Guid]::Parse($sessionId)).ToString('N').ToUpperInvariant() }
)

$validJson = @{ sessionId = $sessionId; prompt = '?? debug prompt' } | ConvertTo-Json -Compress
$invalidCases = @(
    @{ Name = 'empty input'; Json = ''; Error = 'hook input is empty' },
    @{ Name = 'whitespace input'; Json = " `t`r`n"; Error = 'hook input is empty' },
    @{ Name = 'malformed JSON'; Json = '{'; Error = 'Failed to parse' },
    @{ Name = 'null input'; Json = 'null'; Error = 'must be a JSON object' },
    @{ Name = 'empty array'; Json = '[]'; Error = 'must be a JSON object' },
    @{ Name = 'single-object array'; Json = "[$validJson]"; Error = 'must be a JSON object' },
    @{ Name = 'multiple-object array'; Json = "[$validJson,$validJson]"; Error = 'must be a JSON object' },
    @{ Name = 'string input'; Json = '"prompt"'; Error = 'must be a JSON object' },
    @{ Name = 'number input'; Json = '42'; Error = 'must be a JSON object' },
    @{ Name = 'boolean input'; Json = 'true'; Error = 'must be a JSON object' },
    @{ Name = 'missing prompt'; Json = "{`"sessionId`":`"$sessionId`"}"; Error = 'does not contain a prompt' },
    @{ Name = 'null prompt'; Json = "{`"sessionId`":`"$sessionId`",`"prompt`":null}"; Error = 'does not contain a prompt' },
    @{ Name = 'empty prompt'; Json = "{`"sessionId`":`"$sessionId`",`"prompt`":`"`"}"; Error = 'does not contain a prompt' },
    @{ Name = 'whitespace prompt'; Json = "{`"sessionId`":`"$sessionId`",`"prompt`":`"  `"}"; Error = 'does not contain a prompt' },
    @{ Name = 'missing session ID'; Json = '{"prompt":"?? debug prompt"}'; Error = 'Invalid session ID' },
    @{ Name = 'invalid session ID'; Json = '{"sessionId":"not-a-guid","prompt":"?? debug prompt"}'; Error = 'Invalid session ID' },
    @{ Name = 'unsupported session_id'; Json = "{`"session_id`":`"$sessionId`",`"prompt`":`"?? debug prompt`"}"; Error = 'Invalid session ID' }
)

try {
    foreach ($case in $validCases) {
        $payload = @{
            sessionId = if ($case.ContainsKey('SessionId')) { $case.SessionId } else { $sessionId }
            prompt = $case.Prompt
        }
        $startedAt = [DateTimeOffset]::UtcNow
        $result = Invoke-HookInput -InputJson ($payload | ConvertTo-Json -Compress)
        if ($result.ExitCode -ne 0 -or $result.Error -or $result.Output) {
            throw "Hook failed for '$($case.Name)': exit $($result.ExitCode), stdout: $($result.Output), stderr: $($result.Error)"
        }

        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($state.enabled -isnot [bool] -or $state.enabled -ne $case.Enabled) {
            throw "Unexpected debug mode for '$($case.Name)': $($state.enabled)"
        }
        $updatedAt = [DateTimeOffset]::MinValue
        if ($state.updatedAt -is [DateTime] -or $state.updatedAt -is [DateTimeOffset]) {
            $updatedAt = [DateTimeOffset]$state.updatedAt
        } elseif (-not [DateTimeOffset]::TryParse($state.updatedAt, [ref]$updatedAt)) {
            throw "Invalid update timestamp for '$($case.Name)': $($state.updatedAt)"
        }
        if ($updatedAt -lt $startedAt -or $updatedAt -gt [DateTimeOffset]::UtcNow) {
            throw "Invalid update timestamp for '$($case.Name)': $($state.updatedAt)"
        }
        $stateBytes = [System.IO.File]::ReadAllBytes($statePath)
        if ($stateBytes.Length -ge 3 -and
            $stateBytes[0] -eq 0xEF -and $stateBytes[1] -eq 0xBB -and $stateBytes[2] -eq 0xBF) {
            throw "State file contains a UTF-8 BOM for '$($case.Name)'."
        }
    }

    $stateBefore = [System.IO.File]::ReadAllText($statePath)
    foreach ($case in $invalidCases) {
        $result = Invoke-HookInput -InputJson $case.Json
        if ($result.ExitCode -ne 2 -or $result.Output -or $result.Error -notmatch $case.Error) {
            throw "Unexpected rejection for '$($case.Name)': exit $($result.ExitCode), stdout: $($result.Output), stderr: $($result.Error)"
        }
        if ([System.IO.File]::ReadAllText($statePath) -cne $stateBefore) {
            throw "Invalid input changed the state file for '$($case.Name)'."
        }
    }

    Write-Output "Passed $($validCases.Count + $invalidCases.Count) check-debug-mode tests."
} finally {
    if (Test-Path -LiteralPath $statePath) {
        Remove-Item -LiteralPath $statePath -Force
    }
}
