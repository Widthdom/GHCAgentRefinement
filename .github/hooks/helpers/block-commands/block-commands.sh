#!/bin/sh

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.14 or later is required to enforce the repository command policy. Tool execution denied.' >&2
    printf '%s\n' '{"permissionDecision":"deny","permissionDecisionReason":"Python 3.14 or later is required to enforce the repository command policy. Tool execution denied."}'
    exit 2
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)' >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.14 or later is required to enforce the repository command policy. Tool execution denied.' >&2
    printf '%s\n' '{"permissionDecision":"deny","permissionDecisionReason":"Python 3.14 or later is required to enforce the repository command policy. Tool execution denied."}'
    exit 2
fi

exec python3 "${0%/*}/block-commands.py" "$@"
