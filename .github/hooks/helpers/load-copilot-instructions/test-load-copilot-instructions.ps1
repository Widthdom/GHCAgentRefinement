$ErrorActionPreference = 'Stop'

$hookPath = Join-Path $PSScriptRoot 'load-copilot-instructions.ps1'
$githubDirectory = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$realInstructionsPath = Join-Path $githubDirectory 'copilot-instructions.md'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Invoke-Hook {
    param(
        [Parameter(Mandatory)]
        [string]$ScriptPath
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = (Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardErrorEncoding = $utf8NoBom
    foreach ($argument in @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath)) {
        $startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        $null = $process.Start()
        $process.StandardInput.Close()
        # StreamReaderはBOMを読み飛ばすため、BOMの有無を検査できるよう標準出力をバイト列で受け取る。
        $outputStream = [System.IO.MemoryStream]::new()
        $outputTask = $process.StandardOutput.BaseStream.CopyToAsync($outputStream)
        $errorTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $outputTask.GetAwaiter().GetResult()
        $outputBytes = $outputStream.ToArray()
        [PSCustomObject]@{
            ExitCode = $process.ExitCode
            OutputBytes = $outputBytes
            Output = $utf8NoBom.GetString($outputBytes)
            Error = $errorTask.GetAwaiter().GetResult()
        }
    } finally {
        $process.Dispose()
    }
}

# 本物と同じ階層（.github/hooks/helpers/load-copilot-instructions）を一時領域に再現し、
# 実ファイルに触れずに指示ファイルの有無や内容を差し替えて検証する。
function New-HookSandbox {
    $root = Join-Path ([System.IO.Path]::GetTempPath()) "ghc-agent-refinement-test-$([System.Guid]::NewGuid().ToString('N'))"
    $hookDirectory = Join-Path $root '.github\hooks\helpers\load-copilot-instructions'
    $null = New-Item -ItemType Directory -Path $hookDirectory -Force
    Copy-Item -LiteralPath $hookPath -Destination $hookDirectory
    [PSCustomObject]@{
        Root = $root
        ScriptPath = Join-Path $hookDirectory 'load-copilot-instructions.ps1'
        InstructionsPath = Join-Path $root '.github\copilot-instructions.md'
    }
}

function Assert-ValidOutput {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        $Result,
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$ExpectedContext
    )

    if ($Result.ExitCode -ne 0 -or $Result.Error) {
        throw "Hook failed for '$Name': exit $($Result.ExitCode), stderr: $($Result.Error)"
    }
    $bytes = $Result.OutputBytes
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "Output contains a UTF-8 BOM for '$Name'."
    }
    $lines = @($Result.Output -split "`r?`n" | Where-Object { $_ -ne '' })
    if ($lines.Count -ne 1) {
        throw "Output is not a single JSON line for '$Name': $($Result.Output)"
    }
    $json = $lines[0] | ConvertFrom-Json
    $propertyNames = @($json.PSObject.Properties.Name)
    if ($propertyNames.Count -ne 1 -or $propertyNames[0] -cne 'additionalContext') {
        throw "Unexpected output properties for '$Name': $($propertyNames -join ', ')"
    }
    if ($json.additionalContext -cne $ExpectedContext) {
        throw "additionalContext does not match the instructions file for '$Name'."
    }
}

$sandboxes = [System.Collections.Generic.List[object]]::new()
$testsPassed = 0

try {
    # 実際の指示ファイルがそのまま渡る。
    $expected = [System.IO.File]::ReadAllText($realInstructionsPath, $utf8NoBom)
    Assert-ValidOutput -Name 'real instructions file' -Result (Invoke-Hook -ScriptPath $hookPath) -ExpectedContext $expected
    $testsPassed++

    $validCases = @(
        @{ Name = 'Japanese text'; Content = "# 指示`n- 応答は日本語を用いる。`n"; Bytes = $null },
        @{ Name = 'CRLF line endings'; Content = "line1`r`nline2`r`n"; Bytes = $null },
        @{ Name = 'JSON special characters'; Content = "quote `" backslash \ tab `t slash / <tag> & 'single'"; Bytes = $null },
        @{ Name = 'emoji (surrogate pair)'; Content = "絵文字 😀 を含む"; Bytes = $null },
        @{ Name = 'UTF-8 BOM is not part of the content'; Content = "# 指示`n"
           Bytes = [byte[]]@(0xEF, 0xBB, 0xBF) + $utf8NoBom.GetBytes("# 指示`n") }
    )
    foreach ($case in $validCases) {
        $sandbox = New-HookSandbox
        $sandboxes.Add($sandbox)
        $bytes = if ($null -ne $case.Bytes) { $case.Bytes } else { $utf8NoBom.GetBytes($case.Content) }
        [System.IO.File]::WriteAllBytes($sandbox.InstructionsPath, $bytes)
        Assert-ValidOutput -Name $case.Name -Result (Invoke-Hook -ScriptPath $sandbox.ScriptPath) -ExpectedContext $case.Content
        $testsPassed++
    }

    $invalidCases = @(
        @{ Name = 'missing instructions file'; Bytes = $null; Error = 'instructions file not found' },
        @{ Name = 'empty instructions file'; Bytes = [byte[]]@(); Error = 'instructions file is empty' },
        @{ Name = 'whitespace-only instructions file'; Bytes = $utf8NoBom.GetBytes(" `t`r`n"); Error = 'instructions file is empty' },
        @{ Name = 'BOM-only instructions file'; Bytes = [byte[]]@(0xEF, 0xBB, 0xBF); Error = 'instructions file is empty' }
    )
    foreach ($case in $invalidCases) {
        $sandbox = New-HookSandbox
        $sandboxes.Add($sandbox)
        if ($null -ne $case.Bytes) {
            [System.IO.File]::WriteAllBytes($sandbox.InstructionsPath, $case.Bytes)
        }
        $result = Invoke-Hook -ScriptPath $sandbox.ScriptPath
        if ($result.ExitCode -ne 2 -or $result.Output -or $result.Error -notmatch $case.Error) {
            throw "Unexpected result for '$($case.Name)': exit $($result.ExitCode), stdout: $($result.Output), stderr: $($result.Error)"
        }
        $testsPassed++
    }

    # ディレクトリは「ファイルとして存在する」条件を満たさないため、未検出として拒否される。
    $sandbox = New-HookSandbox
    $sandboxes.Add($sandbox)
    $null = New-Item -ItemType Directory -Path $sandbox.InstructionsPath
    $result = Invoke-Hook -ScriptPath $sandbox.ScriptPath
    if ($result.ExitCode -ne 2 -or $result.Output -or $result.Error -notmatch 'instructions file not found') {
        throw "Unexpected result for 'directory instead of file': exit $($result.ExitCode), stdout: $($result.Output), stderr: $($result.Error)"
    }
    $testsPassed++

    Write-Output "Passed $testsPassed load-copilot-instructions tests."
} finally {
    foreach ($sandbox in $sandboxes) {
        if (Test-Path -LiteralPath $sandbox.Root) {
            Remove-Item -LiteralPath $sandbox.Root -Recurse -Force
        }
    }
}
