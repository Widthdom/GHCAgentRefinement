#!/bin/sh

# Claude Codeのhook設定（.claude/settings.json）から run_claude_hook.py を起動する。
# Claude Codeのhook設定はOS別のコマンドを持てないため、Pythonのコマンド名をここで選ぶ。
# Copilotのhook定義と同じく、Windows（Git Bash）では python、macOS/Linuxでは python3 を使う。
case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) python=python ;;
    *) python=python3 ;;
esac

# ${0%/*}：本スクリプトのパスから末尾のファイル名を除いたディレクトリ
# exec：シェルをPythonのプロセスに置き換え、標準入出力と終了コードをそのまま引き継ぐ
exec "$python" "${0%/*}/run_claude_hook.py"
