param(
    [Parameter(Mandatory)]
    [string[]]$Hooks,

    [Parameter(Mandatory)]
    [string[]]$Skills,

    [Parameter(Mandatory)]
    [string]$CurrentJst
)

$ErrorActionPreference = 'Stop'

$hooksHeader = [regex]::Unescape('\u4F7F\u7528\u3057\u305Fhook')
$skillsHeader = [regex]::Unescape('\u4F7F\u7528\u3057\u305Fskill')
$timeHeader = [regex]::Unescape('\u73FE\u5728\u6642\u523B')
$hookLines = ($Hooks | ForEach-Object { "- $_" }) -join [Environment]::NewLine
$skillLines = ($Skills | ForEach-Object { "- $_" }) -join [Environment]::NewLine

@"
## ${hooksHeader}:
$hookLines
## ${skillsHeader}:
$skillLines
## $timeHeader
$CurrentJst
"@
