import os
import runpy

# 編集ツールの呼び出し1件を、ツールの実行前に再現し、書き込み先のファイルごとに編集前と編集後のファイル全体の内容を求める。
# 編集後のファイル全体を検査するpreToolUse hookが共有する。

# 引数名・書き込みではないコマンド・パッチの書式は、extract-edit-tool-changes utilityと共有する。
_extract_edit_tool_changes = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
))
_PATCH_FILE_PATTERN = _extract_edit_tool_changes['PATCH_FILE_PATTERN']
_PATCH_MOVE_PATTERN = _extract_edit_tool_changes['PATCH_MOVE_PATTERN']
_NON_WRITE_COMMANDS = _extract_edit_tool_changes['NON_WRITE_COMMANDS']
_REMOVED_TEXT_KEYS = _extract_edit_tool_changes['REMOVED_TEXT_KEYS']
_NEW_TEXT_KEYS = _extract_edit_tool_changes['NEW_TEXT_KEYS']
_FULL_CONTENT_KEYS = _extract_edit_tool_changes['FULL_CONTENT_KEYS']
_get_first_string = _extract_edit_tool_changes['get_first_string']
_get_path_argument = _extract_edit_tool_changes['get_path_argument']


class _UndeterminedContent(Exception):
    # 編集後の内容を特定できない場合（置換前の内容が見つからないなど）。ツール自体が失敗するため結果に含めない。
    pass


def _read_current(path):
    # 改行コードを変換せずに読み、置換前の内容と照合できるようにする。ファイルがなければNone。
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8-sig', newline='') as file:
            return file.read()
    except UnicodeDecodeError:
        raise _UndeterminedContent() from None


def _replace_once(current, old, new):
    if current is None or not isinstance(old, str) or not old or old not in current:
        raise _UndeterminedContent()
    return current.replace(old, new if isinstance(new, str) else '', 1)


def _insert_after_line(current, line_number, text):
    # insert_line行目の後に挿入する。0はファイルの先頭。
    if current is None or type(line_number) is not int or not isinstance(text, str):
        raise _UndeterminedContent()
    lines = current.splitlines(keepends=True)
    if not 0 <= line_number <= len(lines):
        raise _UndeterminedContent()
    if line_number and not lines[line_number - 1].endswith(('\n', '\r')):
        lines[line_number - 1] += '\n'
    if not text.endswith(('\n', '\r')):
        text += '\n'
    return ''.join(lines[:line_number] + [text] + lines[line_number:])


def _find_lines(lines, target, start):
    # target（行のリスト）がlinesに現れる位置をstart以降から探す。末尾の空白の違いは許容する。
    for normalize in (lambda line: line, str.rstrip):
        normalized_target = [normalize(line) for line in target]
        for index in range(start, len(lines) - len(target) + 1):
            if [normalize(line) for line in lines[index:index + len(target)]] == normalized_target:
                return index
    return -1


def _apply_update(current, patch_lines):
    # `*** Update File:` のパッチを `@@` で区切ったハンクごとに適用する。
    if current is None:
        raise _UndeterminedContent()
    lines = current.splitlines()
    hunks = [[]]
    for line in patch_lines:
        if line.startswith('@@'):
            hunks.append([])
        elif line != '*** End of File':
            hunks[-1].append(line)
    cursor = 0
    for hunk in hunks:
        if not hunk:
            continue
        # 空行はパッチ上で先頭の空白が落ちた文脈行として扱う。
        old = [line[1:] for line in hunk if line[:1] in (' ', '-', '')]
        new = [line[1:] for line in hunk if line[:1] in (' ', '+', '')]
        index = _find_lines(lines, old, cursor) if old else len(lines)
        if index < 0:
            raise _UndeterminedContent()
        lines[index:index + len(old)] = new
        cursor = index + len(new)
    return '\n'.join(lines) + '\n'


def _get_patch_contents(text, cwd):
    files = []
    for line in text.splitlines():
        match = _PATCH_FILE_PATTERN.match(line)
        if match:
            files.append({'kind': match.group(1), 'path': match.group(2), 'target': match.group(2), 'lines': []})
        elif files and (move := _PATCH_MOVE_PATTERN.match(line)):
            files[-1]['target'] = move.group(1)
        elif files and line != '*** End Patch':
            files[-1]['lines'].append(line)
    contents = []
    for file in files:
        if file['kind'] == 'Delete':
            continue
        try:
            if file['kind'] == 'Add':
                before = None
                after = '\n'.join(line[1:] for line in file['lines'] if line.startswith('+')) + '\n'
            else:
                # 移動を伴う場合も、移動元の内容を編集前とする。
                before = _read_current(os.path.abspath(os.path.join(cwd, file['path'])))
                after = _apply_update(before, file['lines'])
        except _UndeterminedContent:
            continue
        contents.append((os.path.abspath(os.path.join(cwd, file['target'])), before, after))
    return contents


def _get_edited_content(tool_name, tool_args, current):
    command = tool_args.get('command')
    if tool_name == 'create' or command == 'create':
        file_text = _get_first_string(tool_args, _FULL_CONTENT_KEYS)
        if file_text is None:
            raise _UndeterminedContent()
        return file_text
    new = _get_first_string(tool_args, _NEW_TEXT_KEYS)
    if tool_name == 'insert' or command == 'insert':
        return _insert_after_line(current, tool_args.get('insert_line'), new)
    return _replace_once(current, _get_first_string(tool_args, _REMOVED_TEXT_KEYS), new)


def simulate_edit_tool_changes(tool_name, tool_args, cwd):
    # tool_name：`functions.` 接頭辞を外したツール名。tool_args：JSON文字列の包みを外したツール引数。
    # 戻り値は (書き込み先の絶対パス, 編集前の内容, 編集後の内容) のリスト。編集前の内容は、まだないファイルではNone。
    # 削除するファイルと、編集後の内容を特定できない編集（ツール自体が失敗する）は含めない。
    if tool_name == 'apply_patch':
        texts = [tool_args] if isinstance(tool_args, str) else [
            value for value in (tool_args.values() if isinstance(tool_args, dict) else []) if isinstance(value, str)
        ]
        return [content for text in texts for content in _get_patch_contents(text, cwd)]
    if not isinstance(tool_args, dict) or tool_args.get('command') in _NON_WRITE_COMMANDS:
        return []
    path = _get_path_argument(tool_args)
    if path is None:
        return []
    path = os.path.abspath(os.path.join(cwd, path))
    try:
        current = _read_current(path)
        return [(path, current, _get_edited_content(tool_name, tool_args, current))]
    except _UndeterminedContent:
        return []
