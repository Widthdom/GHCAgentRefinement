$ErrorActionPreference = 'Stop'

$instructionsPath = Join-Path $PSScriptRoot '..\..\..\copilot-instructions.md'
if (-not (Test-Path -LiteralPath $instructionsPath -PathType Leaf)) {
    [Console]::Error.WriteLine("Copilot instructions file not found: $instructionsPath")
    exit 1
}

$instructions = [System.IO.File]::ReadAllText($instructionsPath, [System.Text.Encoding]::UTF8)
if ([string]::IsNullOrWhiteSpace($instructions)) {
    [Console]::Error.WriteLine("Copilot instructions file is empty: $instructionsPath")
    exit 1
}

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$output = @{ additionalContext = $instructions } | ConvertTo-Json -Compress
[Console]::Out.WriteLine($output)
