$ErrorActionPreference = 'Stop'

$hookPath = Join-Path $PSScriptRoot 'debug-add-response-footer.ps1'
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('D'))
[System.IO.Directory]::CreateDirectory($tempDir) | Out-Null

$debugStateDir = Join-Path (Join-Path ([System.IO.Path]::GetTempPath()) 'ghc-agent-refinement') 'debug-mode'
[System.IO.Directory]::CreateDirectory($debugStateDir) | Out-Null

$createdStateFiles = [System.Collections.Generic.List[string]]::new()

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

try {
    $testsPassed = 0

    # 1. 入力異常系 (exit 2)
    $invalidInputs = @(
        @{ Name = 'Empty input'; Json = '' },
        @{ Name = 'Whitespace input'; Json = "   `t`n" },
        @{ Name = 'Invalid JSON'; Json = '{invalid-json}' },
        @{ Name = 'Array JSON'; Json = '[]' },
        @{ Name = 'String JSON'; Json = '"not-an-object"' }
    )

    foreach ($case in $invalidInputs) {
        $result = Invoke-HookInput -InputJson $case.Json
        if ($result.ExitCode -ne 2) {
            throw "Expected exit 2 for '$($case.Name)', got $($result.ExitCode). Output: $($result.Output), Error: $($result.Error)"
        }
        $testsPassed++
    }

    # 2. transcriptPath と sessionId の異常系 (exit 2)
    $validGuid = [System.Guid]::NewGuid().ToString('D')
    $validTranscript = Join-Path $tempDir 'valid-transcript.jsonl'
    [System.IO.File]::WriteAllText($validTranscript, (@{
        type = 'assistant.message'
        data = @{ content = 'Hello world' }
    } | ConvertTo-Json -Compress) + "`n", [System.Text.UTF8Encoding]::new($false))

    $missingPathCases = @(
        @{ Name = 'Missing transcriptPath'; Json = (@{ sessionId = $validGuid } | ConvertTo-Json -Compress) },
        @{ Name = 'Whitespace transcriptPath'; Json = (@{ sessionId = $validGuid; transcriptPath = '   ' } | ConvertTo-Json -Compress) },
        @{ Name = 'Nonexistent transcriptPath'; Json = (@{ sessionId = $validGuid; transcriptPath = (Join-Path $tempDir 'not-found.jsonl') } | ConvertTo-Json -Compress) },
        @{ Name = 'Invalid sessionId'; Json = (@{ sessionId = 'not-a-guid'; transcriptPath = $validTranscript } | ConvertTo-Json -Compress) }
    )

    foreach ($case in $missingPathCases) {
        $result = Invoke-HookInput -InputJson $case.Json
        if ($result.ExitCode -ne 2) {
            throw "Expected exit 2 for '$($case.Name)', got $($result.ExitCode). Error: $($result.Error)"
        }
        $testsPassed++
    }

    # 3. デバッグ状態ファイルが存在しない場合 (exit 0, 出力なし)
    $nonExistentSessionId = [System.Guid]::NewGuid().ToString('D')
    $noStateJson = @{
        sessionId = $nonExistentSessionId
        transcriptPath = $validTranscript
    } | ConvertTo-Json -Compress
    $result = Invoke-HookInput -InputJson $noStateJson
    if ($result.ExitCode -ne 0 -or -not [string]::IsNullOrEmpty($result.Output.Trim())) {
        throw "Expected exit 0 and empty output when state file does not exist. Got exit $($result.ExitCode), Output: $($result.Output)"
    }
    $testsPassed++

    # 4. デバッグ状態ファイルのパース失敗 (exit 2)
    $corruptSessionId = [System.Guid]::NewGuid().ToString('D')
    $corruptStatePath = Join-Path $debugStateDir "$corruptSessionId.json"
    [System.IO.File]::WriteAllText($corruptStatePath, '{corrupt-json', [System.Text.UTF8Encoding]::new($false))
    $createdStateFiles.Add($corruptStatePath)

    $corruptStateJson = @{
        sessionId = $corruptSessionId
        transcriptPath = $validTranscript
    } | ConvertTo-Json -Compress
    $result = Invoke-HookInput -InputJson $corruptStateJson
    if ($result.ExitCode -ne 2) {
        throw "Expected exit 2 for corrupt state file. Got exit $($result.ExitCode), Error: $($result.Error)"
    }
    $testsPassed++

    # 5. enabled が [bool] 以外 (exit 0, 出力なし - 安全側に倒す)
    $nonBoolCases = @(
        @{ Name = 'String "true"'; Value = 'true' },
        @{ Name = 'String "false"'; Value = 'false' },
        @{ Name = 'Number 1'; Value = 1 },
        @{ Name = 'Null'; Value = $null }
    )
    foreach ($case in $nonBoolCases) {
        $sId = [System.Guid]::NewGuid().ToString('D')
        $sPath = Join-Path $debugStateDir "$sId.json"
        [System.IO.File]::WriteAllText($sPath, (@{ enabled = $case.Value } | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false))
        $createdStateFiles.Add($sPath)

        $inputJson = @{ sessionId = $sId; transcriptPath = $validTranscript } | ConvertTo-Json -Compress
        $result = Invoke-HookInput -InputJson $inputJson
        if ($result.ExitCode -ne 0 -or -not [string]::IsNullOrEmpty($result.Output.Trim())) {
            throw "Expected exit 0 and empty output for non-bool enabled '$($case.Name)'. Got exit $($result.ExitCode), Output: $($result.Output)"
        }
        $testsPassed++
    }

    # 6. enabled が false (exit 0, 出力なし)
    $disabledSessionId = [System.Guid]::NewGuid().ToString('D')
    $disabledStatePath = Join-Path $debugStateDir "$disabledSessionId.json"
    [System.IO.File]::WriteAllText($disabledStatePath, (@{ enabled = $false } | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false))
    $createdStateFiles.Add($disabledStatePath)

    $disabledJson = @{ sessionId = $disabledSessionId; transcriptPath = $validTranscript } | ConvertTo-Json -Compress
    $result = Invoke-HookInput -InputJson $disabledJson
    if ($result.ExitCode -ne 0 -or -not [string]::IsNullOrEmpty($result.Output.Trim())) {
        throw "Expected exit 0 and empty output when enabled is false. Got exit $($result.ExitCode), Output: $($result.Output)"
    }
    $testsPassed++

    # 7. トランスクリプト内容の異常 (exit 2)
    # 7-1. JSON パースエラー行
    $badTranscript = Join-Path $tempDir 'bad-line.jsonl'
    [System.IO.File]::WriteAllText($badTranscript, "{bad-json`n", [System.Text.UTF8Encoding]::new($false))
    $enabledSessionId = [System.Guid]::NewGuid().ToString('D')
    $enabledStatePath = Join-Path $debugStateDir "$enabledSessionId.json"
    [System.IO.File]::WriteAllText($enabledStatePath, (@{ enabled = $true } | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false))
    $createdStateFiles.Add($enabledStatePath)

    $badTranscriptJson = @{ sessionId = $enabledSessionId; transcriptPath = $badTranscript } | ConvertTo-Json -Compress
    $result = Invoke-HookInput -InputJson $badTranscriptJson
    if ($result.ExitCode -ne 2) {
        throw "Expected exit 2 for bad transcript line. Got exit $($result.ExitCode), Error: $($result.Error)"
    }
    $testsPassed++

    # 7-2. assistant.message が存在しない
    $noAssistantTranscript = Join-Path $tempDir 'no-assistant.jsonl'
    [System.IO.File]::WriteAllText($noAssistantTranscript, (@{
        type = 'user.message'
        data = @{ content = 'Hello' }
    } | ConvertTo-Json -Compress) + "`n", [System.Text.UTF8Encoding]::new($false))

    $noAssistantJson = @{ sessionId = $enabledSessionId; transcriptPath = $noAssistantTranscript } | ConvertTo-Json -Compress
    $result = Invoke-HookInput -InputJson $noAssistantJson
    if ($result.ExitCode -ne 2) {
        throw "Expected exit 2 for transcript without assistant.message. Got exit $($result.ExitCode), Error: $($result.Error)"
    }
    $testsPassed++

    # 8. デバッグモード有効時: フッターがない場合にブロック判定を返す (exit 0, decision: block)
    $validBlockJson = @{ sessionId = $enabledSessionId; transcriptPath = $validTranscript } | ConvertTo-Json -Compress
    $result = Invoke-HookInput -InputJson $validBlockJson
    if ($result.ExitCode -ne 0) {
        throw "Expected exit 0 for valid run with missing footer. Got exit $($result.ExitCode), Error: $($result.Error)"
    }
    $blockOutput = $result.Output | ConvertFrom-Json
    if ($blockOutput.decision -ne 'block' -or [string]::IsNullOrWhiteSpace($blockOutput.reason)) {
        throw "Expected decision 'block' with non-empty reason. Got output: $($result.Output)"
    }
    $testsPassed++

    # 9. デバッグモード有効時: 正しいフッターが既に存在する場合は何もしない (exit 0, 出力なし)
    $currentJst = & (Join-Path $PSScriptRoot 'get-current-jst.ps1')
    $formattedFooter = & (Join-Path $PSScriptRoot 'format-response-footer.ps1') `
        -Hooks @('none') `
        -Skills @('none') `
        -CurrentJst $currentJst

    $validWithFooterTranscript = Join-Path $tempDir 'with-footer.jsonl'
    [System.IO.File]::WriteAllText($validWithFooterTranscript, (@{
        type = 'assistant.message'
        data = @{ content = "Here is the response.`n`n$formattedFooter" }
    } | ConvertTo-Json -Compress) + "`n", [System.Text.UTF8Encoding]::new($false))

    $withFooterJson = @{ sessionId = $enabledSessionId; transcriptPath = $validWithFooterTranscript } | ConvertTo-Json -Compress
    $result = Invoke-HookInput -InputJson $withFooterJson
    if ($result.ExitCode -ne 0 -or -not [string]::IsNullOrEmpty($result.Output.Trim())) {
        throw "Expected exit 0 and empty output when valid footer exists. Got exit $($result.ExitCode), Output: $($result.Output)"
    }
    $testsPassed++

    Write-Output "Passed $testsPassed test-debug-add-response-footer tests."
} finally {
    if (Test-Path -LiteralPath $tempDir) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    foreach ($file in $createdStateFiles) {
        if (Test-Path -LiteralPath $file) {
            Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
        }
    }
}
