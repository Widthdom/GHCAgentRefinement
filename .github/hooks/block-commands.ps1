param(
    [string]$DefinitionsPath = (Join-Path $PSScriptRoot 'helpers\block-commands\blocked-command-patterns.json')
)

$ErrorActionPreference = 'Stop'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$inputJson = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($inputJson)) {
    [Console]::Error.WriteLine('preToolUse hook input is empty.')
    exit 2
}

try {
    $hookInput = $inputJson | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine("Failed to parse preToolUse hook input: $($_.Exception.Message)")
    exit 2
}

$toolArgs = if ($null -ne $hookInput.toolArgs) {
    $hookInput.toolArgs
} else {
    $hookInput.tool_input
}
$command = if ($toolArgs -is [string]) {
    $toolArgs
} elseif ($null -ne $toolArgs -and $toolArgs.command -is [string]) {
    $toolArgs.command
} else {
    [Console]::Error.WriteLine('preToolUse hook input does not contain a command string.')
    exit 2
}

if (-not (Test-Path -LiteralPath $DefinitionsPath -PathType Leaf)) {
    [Console]::Error.WriteLine("Blocked command definitions not found: $DefinitionsPath")
    exit 2
}

try {
    $definitions = Get-Content -LiteralPath $DefinitionsPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine("Failed to parse blocked command definitions: $($_.Exception.Message)")
    exit 2
}

foreach ($definition in $definitions) {
    if ([string]::IsNullOrWhiteSpace($definition.name) -or
        [string]::IsNullOrWhiteSpace($definition.pattern) -or
        [string]::IsNullOrWhiteSpace($definition.reason)) {
        [Console]::Error.WriteLine('Each blocked command definition requires name, pattern, and reason.')
        exit 2
    }

    try {
        $isMatch = [regex]::IsMatch(
            $command,
            $definition.pattern,
            [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
    } catch {
        [Console]::Error.WriteLine(
            "Invalid regular expression in blocked command definition '$($definition.name)': $($_.Exception.Message)"
        )
        exit 2
    }

    if ($isMatch) {
        @{
            permissionDecision = 'deny'
            permissionDecisionReason = $definition.reason
        } | ConvertTo-Json -Compress
        exit 0
    }
}

'{}'
