param(
    [Parameter(Mandatory)]
    [string]$Response,

    [Parameter(Mandatory)]
    [string[]]$ExpectedHooks,

    [Parameter(Mandatory)]
    [string[]]$ExpectedSkills,

    [Parameter(Mandatory)]
    [string]$CurrentJst
)

$ErrorActionPreference = 'Stop'

$footerPattern = '(?s)## \u4F7F\u7528\u3057\u305Fhook\uFF08\u30A4\u30D9\u30F3\u30C8\u5C65\u6B74\u304B\u3089\u63A8\u5B9A\uFF09:\r?\n(?<hooks>(?:- [^\r\n]+\r?\n)+)## \u4F7F\u7528\u3057\u305Fskill\uFF08\u30A4\u30D9\u30F3\u30C8\u5C65\u6B74\u304B\u3089\u63A8\u5B9A\uFF09:\r?\n(?<skills>(?:- [^\r\n]+\r?\n)+)## \u73FE\u5728\u6642\u523B\r?\n(?<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) JST\r?\n\r?\n\z'
$footerMatch = [regex]::Match($Response, $footerPattern)
if (-not $footerMatch.Success) {
    return $false
}

$footerHooks = @(
    [regex]::Matches($footerMatch.Groups['hooks'].Value, '(?m)^- (?<hook>[^\r\n]+)\r?$') |
        ForEach-Object { $_.Groups['hook'].Value }
)
$hooksAreCorrect =
    $footerHooks.Count -eq $ExpectedHooks.Count -and
    $null -eq (Compare-Object -ReferenceObject $ExpectedHooks -DifferenceObject $footerHooks)

$footerSkills = @(
    [regex]::Matches($footerMatch.Groups['skills'].Value, '(?m)^- (?<skill>[^\r\n]+)\r?$') |
        ForEach-Object { $_.Groups['skill'].Value }
)
$skillsAreCorrect =
    $footerSkills.Count -eq $ExpectedSkills.Count -and
    $null -eq (Compare-Object -ReferenceObject $ExpectedSkills -DifferenceObject $footerSkills)

$timestampFormat = 'yyyy-MM-dd HH:mm:ss'
$currentTimestamp = [DateTime]::ParseExact(
    $CurrentJst -replace ' JST$',
    $timestampFormat,
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::None
)
$footerTimestamp = [DateTime]::ParseExact(
    $footerMatch.Groups['timestamp'].Value,
    $timestampFormat,
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::None
)
$age = $currentTimestamp - $footerTimestamp
$timestampIsCurrent = $age.TotalMinutes -ge -2 -and $age.TotalMinutes -le 10

$hooksAreCorrect -and $skillsAreCorrect -and $timestampIsCurrent
