$jst = [TimeZoneInfo]::FindSystemTimeZoneById('Tokyo Standard Time')
[TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $jst).ToString("yyyy-MM-dd HH:mm:ss 'JST'")