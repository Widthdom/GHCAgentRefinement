import os
import runpy

# 依頼中の編集で新たに持ち込んだ行（introduced lines）のうち、正規表現に一致するものを探す。
# デバッグ用のコードや仮実装を残す応答を差し戻すagentStop hook（detect-temporary-code）が使う。

# 他のutilityの処理は、スクリプトから読み込む。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']
# インデントの違いで既存の内容との照合が外れないよう、各行は前後の空白を除いて比べる。
_stripped_lines = runpy.run_path(os.path.join(_utilities_directory, 'get-stripped-lines', 'get-stripped-lines.py'))


def _read_lines(path):
    text = _read_stored_file_content_for_write_comparison(path)
    return None if text is None else _stripped_lines['get_stripped_lines'](text)


def find_introduced_lines_by_regex(changes, cwd, excluded_file_patterns, named_regexes):
    # introducedとは、この依頼で新たに書き込み（置換前の内容になかった行）、最終的なファイルに残っている行を指す。
    # changes：edit-tool-changes utilityのextract_edit_tool_changesが返す変更のリスト。依頼中の編集を古い順に並べたもの。
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
        display_path = _get_display_path(full_path, real_cwd)
        if any(pattern.search(display_path) for pattern in excluded_file_patterns):
            continue
        written = change['written']
        removed_lines = _stripped_lines['get_stripped_lines'](change['removed'])
        for name, regex in named_regexes:
            for match in regex.finditer(written):
                line = _stripped_lines['get_stripped_line_at'](written, match.start())
                if line in removed_lines or (display_path, line) in found:
                    continue
                if full_path not in current_lines:
                    current_lines[full_path] = _read_lines(full_path) if os.path.isfile(full_path) else None
                lines = current_lines[full_path]
                if lines is not None and line in lines:
                    found.add((display_path, line))
                    findings.append((display_path, name, line))
    return findings
