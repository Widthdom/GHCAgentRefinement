param(
    [Parameter(Mandatory)]
    [string[]]$Hooks,

    [Parameter(Mandatory)]
    [string[]]$Skills,

    [Parameter(Mandatory)]
    [string]$CurrentJst
)

$ErrorActionPreference = 'Stop'

$hooksHeader = [regex]::Unescape('\u4F7F\u7528\u3057\u305Fhook\uFF08\u30A4\u30D9\u30F3\u30C8\u5C65\u6B74\u304B\u3089\u63A8\u5B9A\uFF09')
$skillsHeader = [regex]::Unescape('\u4F7F\u7528\u3057\u305Fskill\uFF08\u30A4\u30D9\u30F3\u30C8\u5C65\u6B74\u304B\u3089\u63A8\u5B9A\uFF09')
$timeHeader = [regex]::Unescape('\u73FE\u5728\u6642\u523B')
$hookLines = ($Hooks | ForEach-Object { "- $_" }) -join [Environment]::NewLine
$skillLines = ($Skills | ForEach-Object { "- $_" }) -join [Environment]::NewLine
$footerBoundary = [char]0x200B

$footer = @"
$footerBoundary
## ${hooksHeader}:
$hookLines
## ${skillsHeader}:
$skillLines
## $timeHeader
$CurrentJst
"@
$footer + ([Environment]::NewLine * 2)
