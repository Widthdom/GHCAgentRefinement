if command -v pwsh >/dev/null 2>&1 &&
    pwsh -NoProfile -NonInteractive -Command \
        'if ($PSVersionTable.PSVersion.Major -ge 7) { exit 0 } else { exit 1 }'
then
    exit 0
fi

echo 'PowerShell 7 or later is required.' >&2
if [ "$(uname -s 2>/dev/null)" = 'Darwin' ]; then
    echo 'Install it with: brew install --cask powershell' >&2
else
    echo 'Installation guide: https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-linux' >&2
fi
exit 2
