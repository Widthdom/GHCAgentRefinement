import os

# 依頼中の編集で新たに持ち込んだ行（introduced lines）のうち、正規表現に一致するものを探す。
# デバッグ用のコードを残す応答を差し戻すagentStop hook（detect-debug-leftovers）と、
# 仮実装を残す応答を差し戻すagentStop hook（detect-stub-placeholders）が共有する。


def normalize_lines(text):
    # インデントの違いで既存の内容との照合が外れないよう、各行の前後の空白を除く。
    return {line.strip() for line in text.splitlines()}


def read_lines(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return normalize_lines(file.read())
    except OSError:
        return None


def get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示す。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path.replace(os.sep, '/')


def get_line(text, position):
    start = text.rfind('\n', 0, position) + 1
    end = text.find('\n', position)
    return text[start:None if end < 0 else end].strip()


def find_introduced_lines_by_regex(changes, cwd, excluded_file_patterns, named_regexes):
    # introducedとは、この依頼で新たに書き込み（置換前の内容になかった行）、最終的なファイルに残っている行を指す。
    # changes：extract-edit-tool-changesの変更のリスト。依頼中の編集を古い順に並べたもの。
    # excluded_file_patterns：対象外とする表示パスの正規表現。
    # named_regexes：(種類の名前, 正規表現) のリスト。一致した位置を含む行を該当行とする。
    # 戻り値は (表示パス, 種類の名前, 該当行) のリスト。
    # 同じファイルの同じ行は、複数の種類に一致しても、先に一致した種類の1件にまとめる。
    findings = []
    found = set()
    current_lines = {}
    real_cwd = os.path.realpath(cwd)
    for change in changes:
        full_path = os.path.realpath(os.path.join(cwd, change['path']))
        # パターンの照合と表示は、OSによらず `/` 区切りのパスで行う。
        display_path = get_display_path(full_path, real_cwd)
        if any(pattern.search(display_path) for pattern in excluded_file_patterns):
            continue
        written = change['written']
        removed_lines = normalize_lines(change['removed'])
        for name, regex in named_regexes:
            for match in regex.finditer(written):
                line = get_line(written, match.start())
                if line in removed_lines or (display_path, line) in found:
                    continue
                if full_path not in current_lines:
                    current_lines[full_path] = read_lines(full_path) if os.path.isfile(full_path) else None
                lines = current_lines[full_path]
                if lines is not None and line in lines:
                    found.add((display_path, line))
                    findings.append((display_path, name, line))
    return findings
