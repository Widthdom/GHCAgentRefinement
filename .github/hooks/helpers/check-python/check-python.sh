if command -v python3 >/dev/null 2>&1 &&
    python3 "${0%/*}/../common/check-python-version.py" >/dev/null 2>&1
then
    exit 0
fi

echo 'Python 3.14 or later is required, and it must be available as the python3 command.' >&2
if [ "$(uname -s 2>/dev/null)" = 'Darwin' ]; then
    echo 'Install it with: brew install python' >&2
else
    echo 'Installation guide: https://docs.python.org/3/using/unix.html' >&2
fi
exit 2
