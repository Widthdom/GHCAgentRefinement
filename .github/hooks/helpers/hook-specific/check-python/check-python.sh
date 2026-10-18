#!/bin/sh

if ! python3 "${0%/*}/../../common/check-python-version.py" >/dev/null 2>&1; then
    echo 'Python 3.14 or later is required, and it must be available as the python3 command.' >&2
    # macOS の場合は Homebrew でのインストールを推奨
    if [ "$(uname -s 2>/dev/null)" = 'Darwin' ]; then
        echo 'Install it with: brew install python' >&2
    # macOS 以外の場合は公式インストールガイド参照を推奨
    else
        echo 'Installation guide: https://docs.python.org/3/using/unix.html' >&2
    fi
    echo 'If python3 is still unavailable after installation, restart the application or terminal running this hook to refresh PATH.' >&2
    exit 2
fi
exit 0
