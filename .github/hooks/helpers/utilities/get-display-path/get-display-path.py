import os

# 理由やメッセージに示すファイルのパスを作る。
# ファイルを検査・記録するpreToolUse hook、postToolUse hook、agentStop hook、utilityが共有する。


def get_display_path(path, cwd):
    # path・cwd：絶対パス。cwd配下のファイルは相対パス、それ以外は絶対パスで、どちらもOSによらず `/` 区切りで返す。
    # 大文字小文字を区別しないWindowsでも配下と判定できるよう、normcaseで比べる。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.normcase(os.path.commonpath([cwd, path])) == os.path.normcase(cwd):
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path.replace(os.sep, '/')
