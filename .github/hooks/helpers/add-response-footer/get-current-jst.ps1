$ErrorActionPreference = 'Stop'

$currentJst = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
    [DateTimeOffset]::UtcNow,
    'Tokyo Standard Time'
)

$currentJst.ToString("yyyy-MM-dd HH:mm:ss 'JST'")
