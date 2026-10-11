param(
    [Parameter(Mandatory)]
    [string[]]$Skills,

    [Parameter(Mandatory)]
    [string]$CurrentJst
)

$ErrorActionPreference = 'Stop'

$skillsHeader = [regex]::Unescape('\u4F7F\u7528\u3057\u305Fskill')
$timeHeader = [regex]::Unescape('\u73FE\u5728\u6642\u523B')
$skillLines = ($Skills | ForEach-Object { "- $_" }) -join [Environment]::NewLine

@"
## ${skillsHeader}:
$skillLines
## $timeHeader
$CurrentJst
"@
