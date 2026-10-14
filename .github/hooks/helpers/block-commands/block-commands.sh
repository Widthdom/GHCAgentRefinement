#!/bin/sh

if ! command -v pwsh >/dev/null 2>&1; then
    printf '%s\n' 'PowerShell 7 or later is required to enforce the repository command policy. Tool execution denied.' >&2
    printf '%s\n' '{"permissionDecision":"deny","permissionDecisionReason":"PowerShell 7 or later is required to enforce the repository command policy. Tool execution denied."}'
    exit 2
fi

exec pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass \
    -File "${0%/*}/block-commands.ps1" "$@"
