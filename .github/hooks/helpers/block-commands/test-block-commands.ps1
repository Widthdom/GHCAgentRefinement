$ErrorActionPreference = 'Stop'

$hookPath = Join-Path $PSScriptRoot 'block-commands.ps1'
$definitionsPath = Join-Path $PSScriptRoot 'blocked-command-patterns.json'

function Invoke-HookInput {
    param(
        [AllowEmptyString()]
        [string]$InputJson,
        [ValidateSet('ps1', 'cmd', 'sh')]
        [string]$EntryPoint = 'ps1',
        [string]$SearchPath
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
    $arguments = @(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $hookPath, '-DefinitionsPath', $definitionsPath
    )
    if ($EntryPoint -eq 'cmd') {
        $startInfo.FileName = $env:ComSpec
        $startInfo.Arguments = "/d /s /c `"`"$(Join-Path $PSScriptRoot 'block-commands.cmd')`"`""
        $arguments = @()
    } elseif ($EntryPoint -eq 'sh') {
        $startInfo.FileName = $bashPath
        $arguments = @('--noprofile', '--norc', (Join-Path $PSScriptRoot 'block-commands.sh').Replace('\', '/'))
    }
    if ($PSBoundParameters.ContainsKey('SearchPath')) {
        $startInfo.Environment['PATH'] = $SearchPath
    }
    foreach ($argument in $arguments) {
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

function Invoke-Hook {
    param(
        [Parameter(Mandatory)]
        [string]$Command
    )

    $payload = @{
        toolName = 'powershell'
        toolArgs = @{ command = $Command }
    }

    $result = Invoke-HookInput -InputJson ($payload | ConvertTo-Json -Compress -Depth 10)
    if ($result.ExitCode -ne 0 -or $result.Error) {
        throw "Hook failed for command '$Command': exit $($result.ExitCode), $($result.Error)"
    }

    $result.Output | ConvertFrom-Json
}

$testCases = @(
    @{ Command = 'git status'; Expected = $null },
    @{ Command = 'git rebase main'; Expected = 'deny' },
    @{ Command = 'git -C repo rebase --interactive HEAD~2'; Expected = 'deny' },
    @{ Command = 'Write-Host start; git rebase main'; Expected = 'deny' },
    @{ Command = 'git push origin main'; Expected = $null },
    @{ Command = 'git push --force origin main'; Expected = 'deny' },
    @{ Command = 'git push origin main --force-with-lease'; Expected = 'deny' },
    @{ Command = 'git -C repo push -f origin main'; Expected = 'deny' },
    @{ Command = 'Write-Host start; git push --force-if-includes origin main'; Expected = 'deny' },
    @{ Command = 'git reset'; Expected = 'deny' },
    @{ Command = 'git reset --hard HEAD~1'; Expected = 'deny' },
    @{ Command = 'git -C repo reset --soft HEAD^'; Expected = 'deny' },
    @{ Command = 'Write-Host start; git reset HEAD file.txt'; Expected = 'deny' },
    @{ Command = 'git commit -m "normal commit"'; Expected = $null },
    @{ Command = 'git commit --no-edit'; Expected = $null },
    @{ Command = 'git commit --amend'; Expected = 'deny' },
    @{ Command = 'git commit --amend --no-edit'; Expected = 'deny' },
    @{ Command = 'git commit --no-edit --amend'; Expected = 'deny' },
    @{ Command = 'git commit -m "updated message" --amend'; Expected = 'deny' },
    @{ Command = 'git -C "repo path" commit --amend'; Expected = 'deny' },
    @{ Command = 'git.exe -c user.name=test commit --amend'; Expected = 'deny' },
    @{ Command = 'Write-Host start; git commit --amend'; Expected = 'deny' },
    @{ Command = 'git commit --amend; Write-Host done'; Expected = 'deny' },
    @{ Command = 'git commit --amend-invalid'; Expected = $null },
    @{ Command = 'git commit -m "normal"; Write-Host --amend'; Expected = $null }
)

foreach ($testCase in $testCases) {
    $result = Invoke-Hook -Command $testCase.Command
    if ($result.permissionDecision -ne $testCase.Expected) {
        throw "Unexpected decision for '$($testCase.Command)': '$($result.permissionDecision)'"
    }
}

$payloadCases = @(
    @{ Name = 'SDK read'; Json = '{"toolName":"view","toolArgs":{"path":"README.md"}}' },
    @{ Name = 'SDK search'; Json = '{"toolName":"rg","toolArgs":{"pattern":"git reset"}}' },
    @{ Name = 'SDK edit string'; Json = '{"toolName":"apply_patch","toolArgs":"git reset"}' },
    @{ Name = 'non-command without args'; Json = '{"toolName":"view"}' },
    @{ Name = 'anchored tool name'; Json = '{"toolName":"not_bash","toolArgs":{"command":"git reset"}}' },
    @{ Name = 'raw command'; Json = '{"toolName":"bash","toolArgs":"git reset"}'; Expected = 'deny' },
    @{ Name = 'prefixed bash'; Json = '{"toolName":"functions.bash","toolArgs":{"command":"git reset"}}'; Expected = 'deny' },
    @{ Name = 'prefixed PowerShell'; Json = '{"toolName":"functions.powershell","toolArgs":{"command":"git reset"}}'; Expected = 'deny' },
    @{ Name = 'Japanese command'; Json = '{"toolName":"bash","toolArgs":{"command":"echo 日本語; git reset"}}'; Expected = 'deny' },
    @{ Name = 'non-command batch'; Json = '{"toolCalls":[{"name":"view","args":{"path":"README.md"}},{"name":"apply_patch","args":"git reset"}]}' },
    @{ Name = 'safe mixed batch'; Json = '{"toolCalls":[{"name":"view"},{"name":"bash","args":{"command":"git status"}},{"name":"powershell","args":"git push origin main"}]}' },
    @{ Name = 'later denied command'; Json = '{"toolCalls":[{"name":"view"},{"name":"bash","args":{"command":"git status"}},{"name":"powershell","args":{"command":"git reset"}}]}'; Expected = 'deny' },
    @{ Name = 'denied command before non-command'; Json = '{"toolCalls":[{"name":"bash","args":{"command":"git reset"}},{"name":"view"}]}'; Expected = 'deny' },
    @{ Name = 'missing command'; Json = '{"toolName":"bash","toolArgs":{}}'; Error = 'requires a non-empty command string' },
    @{ Name = 'missing args'; Json = '{"toolName":"powershell"}'; Error = 'requires a non-empty command string' },
    @{ Name = 'null args'; Json = '{"toolName":"bash","toolArgs":null}'; Error = 'requires a non-empty command string' },
    @{ Name = 'numeric command'; Json = '{"toolName":"bash","toolArgs":{"command":42}}'; Error = 'requires a non-empty command string' },
    @{ Name = 'array command'; Json = '{"toolName":"bash","toolArgs":{"command":["git status"]}}'; Error = 'requires a non-empty command string' },
    @{ Name = 'array args'; Json = '{"toolName":"bash","toolArgs":[{"command":"git reset"}]}'; Error = 'requires a non-empty command string' },
    @{ Name = 'empty command'; Json = '{"toolName":"bash","toolArgs":{"command":""}}'; Error = 'requires a non-empty command string' },
    @{ Name = 'blank raw command'; Json = '{"toolName":"bash","toolArgs":" \t\r\n"}'; Error = 'requires a non-empty command string' },
    @{ Name = 'invalid later batch command'; Json = '{"toolCalls":[{"name":"bash","args":{"command":"git status"}},{"name":"powershell","args":{}}]}'; Error = 'requires a non-empty command string' },
    @{ Name = 'missing tool name'; Json = '{"toolArgs":{"command":"git reset"}}'; Error = 'requires a tool name' },
    @{ Name = 'empty tool name'; Json = '{"toolName":"","toolArgs":{}}'; Error = 'requires a tool name' },
    @{ Name = 'numeric tool name'; Json = '{"toolName":42,"toolArgs":{}}'; Error = 'requires a tool name' },
    @{ Name = 'array tool name'; Json = '{"toolName":["bash"],"toolArgs":{"command":"git status"}}'; Error = 'requires a tool name' },
    @{ Name = 'missing batch name'; Json = '{"toolCalls":[{"args":{"command":"git reset"}}]}'; Error = 'requires a tool name' },
    @{ Name = 'null batch entry'; Json = '{"toolCalls":[null]}'; Error = 'requires each tool call to be a JSON object' },
    @{ Name = 'string batch entry'; Json = '{"toolCalls":["bash"]}'; Error = 'requires each tool call to be a JSON object' },
    @{ Name = 'array batch entry'; Json = '{"toolCalls":[[{"name":"bash","args":{"command":"git status"}}]]}'; Error = 'requires each tool call to be a JSON object' },
    @{ Name = 'object batch'; Json = '{"toolCalls":{"name":"bash","args":{"command":"git reset"}}}'; Error = 'must be a non-empty array' },
    @{ Name = 'null batch'; Json = '{"toolCalls":null}'; Error = 'must be a non-empty array' },
    @{ Name = 'empty batch'; Json = '{"toolCalls":[]}'; Error = 'must be a non-empty array' },
    @{ Name = 'null input'; Json = 'null'; Error = 'must be a JSON object' },
    @{ Name = 'array input'; Json = '[]'; Error = 'must be a JSON object' },
    @{ Name = 'single-object array input'; Json = '[{"toolName":"bash","toolArgs":{"command":"git status"}}]'; Error = 'must be a JSON object' },
    @{ Name = 'string input'; Json = '"git reset"'; Error = 'must be a JSON object' },
    @{ Name = 'invalid JSON'; Json = '{'; Error = 'Failed to parse preToolUse hook input' },
    @{ Name = 'empty input'; Json = ''; Error = 'hook input is empty' }
)

foreach ($testCase in $payloadCases) {
    $result = Invoke-HookInput -InputJson $testCase.Json
    if ($testCase.Error) {
        if ($result.ExitCode -ne 2 -or $result.Output -or
            -not $result.Error.Contains($testCase.Error)) {
            throw "Unexpected failure for '$($testCase.Name)': $($result | ConvertTo-Json -Compress)"
        }
        continue
    }

    if ($result.ExitCode -ne 0 -or $result.Error) {
        throw "Unexpected error for '$($testCase.Name)': $($result | ConvertTo-Json -Compress)"
    }
    $output = $result.Output | ConvertFrom-Json
    $decision = $output
    if ($testCase.Expected) {
        if ($decision.permissionDecision -ne $testCase.Expected -or
            [string]::IsNullOrWhiteSpace($decision.permissionDecisionReason)) {
            throw "Unexpected decision for '$($testCase.Name)': $($result.Output)"
        }
    } elseif ($result.Output.Trim() -ne '{}') {
        throw "Unexpected skip/allow output for '$($testCase.Name)': $($result.Output)"
    }
}

Write-Output "Passed $($testCases.Count + $payloadCases.Count) block-command hook tests."

$entryPoints = @()
if ($IsWindows) {
    $entryPoints += 'cmd'
}
$bashCommand = Get-Command bash -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$bashPath = if ($IsWindows) {
    $gitCommand = Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1
    Join-Path (Split-Path (Split-Path $gitCommand.Source)) 'bin\bash.exe'
} elseif ($bashCommand) {
    $bashCommand.Source
}
if ($bashPath -and (Test-Path -LiteralPath $bashPath -PathType Leaf)) {
    $entryPoints += 'sh'
} else {
    Write-Output 'Skipping sh wrapper tests: a native bash executable was not found.'
}

foreach ($entryPoint in $entryPoints) {
    foreach ($testCase in @(
        @{ Json = '{"toolName":"bash","toolArgs":{"command":"git status"}}'; Expected = $null },
        @{ Json = '{"toolName":"powershell","toolArgs":{"command":"echo 日本語; git reset"}}'; Expected = 'deny' }
    )) {
        $result = Invoke-HookInput -InputJson $testCase.Json -EntryPoint $entryPoint
        if ($result.ExitCode -ne 0 -or $result.Error) {
            throw "Unexpected $entryPoint wrapper execution: $($result | ConvertTo-Json -Compress)"
        }
        $output = $result.Output | ConvertFrom-Json
        if ($result.ExitCode -ne 0 -or $result.Error -or $output.permissionDecision -ne $testCase.Expected) {
            throw "Unexpected $entryPoint wrapper decision: $($result | ConvertTo-Json -Compress)"
        }
    }

    $result = Invoke-HookInput -InputJson '{' -EntryPoint $entryPoint
    if ($result.ExitCode -ne 2 -or $result.Output -or -not $result.Error.Contains('Failed to parse')) {
        throw "Unexpected $entryPoint wrapper error forwarding: $($result | ConvertTo-Json -Compress)"
    }

    $result = Invoke-HookInput -InputJson '{}' -EntryPoint $entryPoint -SearchPath ''
    $output = $result.Output | ConvertFrom-Json
    if ($result.ExitCode -ne 2 -or $output.permissionDecision -ne 'deny' -or
        [string]::IsNullOrWhiteSpace($output.permissionDecisionReason) -or
        -not $result.Error.Contains('Tool execution denied.')) {
        throw "Unexpected $entryPoint wrapper missing-pwsh result: $($result | ConvertTo-Json -Compress)"
    }
    Write-Output "Passed 4 $entryPoint wrapper tests."
}
