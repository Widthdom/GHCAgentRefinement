function Test-ResponseFooterCorrectionMessage {
    param(
        [AllowNull()]
        [string]$Content
    )

    -not [string]::IsNullOrWhiteSpace($Content) -and
        $Content.StartsWith(
            'The final response footer is missing or incorrect.',
            [System.StringComparison]::Ordinal
        )
}
