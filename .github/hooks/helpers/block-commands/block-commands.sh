#!/bin/sh

if ! python3 "${0%/*}/../common/check-python-version.py" >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.14 or later is required to enforce the repository command policy. Tool execution denied.' >&2
    printf '%s\n' '{"permissionDecision":"deny","permissionDecisionReason":"Python 3.14 or later is required to enforce the repository command policy. Tool execution denied."}'
    exit 2
fi

exec python3 "${0%/*}/block-commands.py" "$@"
