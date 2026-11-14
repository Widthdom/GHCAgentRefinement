import os
import re
import runpy

# 編集ツールの呼び出し1件を解釈する。編集したファイルを検査・記録するpreToolUse hook、postToolUse hook、
# 依頼中の編集を振り返るagentStop hookが共有する。
#   get_edited_paths：対象のファイルのパスを取り出す。
#   extract_edit_tool_changes：ファイルごとの変更（書き込む内容と置換前の内容）を取り出す。
#   simulate_edit_tool_changes：ツールの実行前に編集を再現し、ファイルごとに編集前と編集後のファイル全体の内容を求める。
# いずれもツール名の `functions.` 接頭辞とJSON文字列に包まれた引数を受け付け、編集ツール以外からは何も取り出さない。

# 他のutilityの処理は、スクリプトから読み込む。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']
_is_edit_tool = runpy.run_path(os.path.join(_utilities_directory, 'check-tool-kinds', 'check-tool-kinds.py'))['is_edit_tool']

# 対象のファイルのパスを表す引数名。`edit`・`view` は `path`、`create` は `file_path` で渡される。
PATH_KEYS = ('path', 'file_path')
# 書き込みではないstr_replace_editorのコマンド。undo_editは書き込む内容を引数に持たない。
NON_WRITE_COMMANDS = frozenset({'view', 'undo_edit'})
# 置換前の内容、新しい内容、作成するファイル全体の内容を表す引数名。
_REMOVED_TEXT_KEYS = ('old_str', 'old_string')
_NEW_TEXT_KEYS = ('new_str', 'new_string')
_FULL_CONTENT_KEYS = ('file_text', 'content')
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+?)\s*$')
_PATCH_MOVE_PATTERN = re.compile(r'^\*\*\* Move to: (.+?)\s*$')
_ALL_PATCH_OPERATIONS = frozenset({'Add File', 'Update File', 'Delete File', 'Move to'})


def get_path_argument(tool_args):
    # PATH_KEYSの順に、空白以外を含む最初の文字列の値を返す。なければNone。編集ツール以外（viewなど）の引数にも使える。
    if not isinstance(tool_args, dict):
        return None
    return next(
        (tool_args[key] for key in PATH_KEYS if isinstance(tool_args.get(key), str) and tool_args[key].strip()), None,
    )


def _get_first_string(tool_args, keys):
    # keysの順に、ツール引数のうち最初に見つかった文字列の値を返す。なければNone。
    return next((tool_args[key] for key in keys if isinstance(tool_args.get(key), str)), None)


def _parse_tool_call(tool_name, raw_tool_args):
    # 編集ツールなら `functions.` 接頭辞を外したツール名と、包みを外した引数を返す。それ以外は (None, None)。
    tool_name = tool_name.removeprefix('functions.')
    if not _is_edit_tool(tool_name):
        return None, None
    return tool_name, _unwrap_tool_args(raw_tool_args)


def _get_command(tool_name, tool_args):
    # apply_patch以外の編集ツールが行う操作を、str_replace_editorのコマンド名で返す。
    # str_replace_editorは `command` 引数、それ以外はツール名で決まる。不明なコマンドは置換とみなす。
    command = tool_args.get('command') if tool_name == 'str_replace_editor' else tool_name
    return command if command in ('create', 'insert', *NON_WRITE_COMMANDS) else 'str_replace'


def _parse_patches(tool_args):
    # 引数の文字列（入れ子を含む）にあるパッチを、ファイルごとの区画に分けて返す。区画は次の項目を持つ辞書。
    #   operation：`Add`・`Update`・`Delete`。path：対象のファイル。moved_to：`*** Move to:` の移動先。なければNone。
    #   hunks：`@@` で区切ったハンクのリスト。各ハンクは本文の行（記号, 内容）のリストで、記号は `+`・`-`・` `。
    #     空行は、パッチ上で先頭の空白が落ちた文脈行として扱う。`*** End of File` などの記号以外の行は含めない。
    sections = []
    for text in _collect_json_string_values(tool_args):
        section = None
        for line in text.splitlines():
            if match := _PATCH_FILE_PATTERN.match(line):
                section = {'operation': match.group(1), 'path': match.group(2), 'moved_to': None, 'hunks': [[]]}
                sections.append(section)
            elif section is None:
                continue
            elif line == '*** End Patch':
                section = None
            elif move := _PATCH_MOVE_PATTERN.match(line):
                section['moved_to'] = move.group(1)
            elif line.startswith('@@'):
                section['hunks'].append([])
            elif line[:1] in ('+', '-', ' '):
                section['hunks'][-1].append((line[0], line[1:]))
            elif not line:
                section['hunks'][-1].append((' ', ''))
    return sections


def get_edited_paths(tool_name, raw_tool_args, patch_operations=_ALL_PATCH_OPERATIONS):
    # patch_operations：apply_patchで対象にする行の種類（`*** Add File:` なら 'Add File'、`*** Move to:` なら 'Move to'）。
    # 戻り値は、ツールに渡された表記のままのパスのリスト。apply_patchはパッチの出現順、それ以外は `path`・`file_path` 引数。
    # str_replace_editorのviewは閲覧なので対象外とする。undo_editはファイルを書き換えるため対象とする。
    tool_name, tool_args = _parse_tool_call(tool_name, raw_tool_args)
    if tool_name == 'apply_patch':
        paths = []
        for section in _parse_patches(tool_args):
            if f"{section['operation']} File" in patch_operations:
                paths.append(section['path'])
            if section['moved_to'] is not None and 'Move to' in patch_operations:
                paths.append(section['moved_to'])
        return paths
    if not isinstance(tool_args, dict) or _get_command(tool_name, tool_args) == 'view':
        return []
    path = get_path_argument(tool_args)
    return [path] if path is not None else []


def extract_edit_tool_changes(tool_name, raw_tool_args, include_deleted=False):
    # 戻り値は次の項目を持つ辞書のリスト。書き込みではない呼び出しでは空。
    #   path：ツールに渡されたファイルのパス。
    #   written：書き込む内容。置換・挿入では新しい内容、パッチでは追加行。
    #   removed：置換前の内容。置換では古い内容、パッチでは削除行。
    #   deleted：パッチによるファイルの削除か。include_deletedがTrueのときだけ結果に含める。
    #   moved_to：パッチ（`*** Move to:`）によるファイルの移動先。移動しない場合はNone。
    #   full_content：ファイル全体を書き込む作成（create）の内容。それ以外はNone。
    #   replacement：置換前後の内容の組（古い内容, 新しい内容）。置換以外はNone。
    #   patch_lines：パッチの本文の行（記号, 内容）のリスト。記号は `+`・`-`・` `。パッチ以外はNone。
    tool_name, tool_args = _parse_tool_call(tool_name, raw_tool_args)
    if tool_name == 'apply_patch':
        changes = []
        for section in _parse_patches(tool_args):
            if section['operation'] == 'Delete' and not include_deleted:
                continue
            patch_lines = [line for hunk in section['hunks'] for line in hunk]
            changes.append({
                'path': section['path'],
                'written': '\n'.join(text for kind, text in patch_lines if kind == '+'),
                'removed': '\n'.join(text for kind, text in patch_lines if kind == '-'),
                'deleted': section['operation'] == 'Delete',
                'moved_to': section['moved_to'],
                'full_content': None,
                'replacement': None,
                'patch_lines': patch_lines,
            })
        return changes
    if not isinstance(tool_args, dict):
        return []
    command = _get_command(tool_name, tool_args)
    path = get_path_argument(tool_args)
    if command in NON_WRITE_COMMANDS or path is None:
        return []
    old = _get_first_string(tool_args, _REMOVED_TEXT_KEYS)
    new = _get_first_string(tool_args, _NEW_TEXT_KEYS)
    # パス・コマンド・置換前の内容を除く引数の文字列を、書き込む内容とする。
    ignored_keys = frozenset({*PATH_KEYS, 'command', *_REMOVED_TEXT_KEYS})
    return [{
        'path': path,
        'written': '\n'.join(_collect_json_string_values(tool_args, ignored_keys)),
        'removed': '\n'.join(text for key in _REMOVED_TEXT_KEYS for text in _collect_json_string_values(tool_args.get(key))),
        'deleted': False,
        'moved_to': None,
        'full_content': _get_first_string(tool_args, _FULL_CONTENT_KEYS) if command == 'create' else None,
        'replacement': (old, new) if old and new is not None else None,
        'patch_lines': None,
    }]


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
    if current is None or not old or old not in current:
        raise _UndeterminedContent()
    return current.replace(old, new if new is not None else '', 1)


def _insert_after_line(current, line_number, text):
    # insert_line行目の後に挿入する。0はファイルの先頭。
    if current is None or type(line_number) is not int or text is None:
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


def _apply_hunks(current, hunks):
    # `*** Update File:` のパッチを、ハンクごとに前から順に適用する。
    if current is None:
        raise _UndeterminedContent()
    lines = current.splitlines()
    cursor = 0
    for hunk in hunks:
        if not hunk:
            continue
        old = [text for kind, text in hunk if kind != '+']
        new = [text for kind, text in hunk if kind != '-']
        index = _find_lines(lines, old, cursor) if old else len(lines)
        if index < 0:
            raise _UndeterminedContent()
        lines[index:index + len(old)] = new
        cursor = index + len(new)
    return '\n'.join(lines) + '\n'


def _simulate_patch_section(section, cwd):
    if section['operation'] == 'Add':
        return None, '\n'.join(text for hunk in section['hunks'] for kind, text in hunk if kind == '+') + '\n'
    # 移動を伴う場合も、移動元の内容を編集前とする。
    before = _read_current(os.path.abspath(os.path.join(cwd, section['path'])))
    return before, _apply_hunks(before, section['hunks'])


def _simulate_command(command, tool_args, current):
    if command == 'create':
        file_text = _get_first_string(tool_args, _FULL_CONTENT_KEYS)
        if file_text is None:
            raise _UndeterminedContent()
        return file_text
    new = _get_first_string(tool_args, _NEW_TEXT_KEYS)
    if command == 'insert':
        return _insert_after_line(current, tool_args.get('insert_line'), new)
    return _replace_once(current, _get_first_string(tool_args, _REMOVED_TEXT_KEYS), new)


def simulate_edit_tool_changes(tool_name, raw_tool_args, cwd):
    # 戻り値は (書き込み先の絶対パス, 編集前の内容, 編集後の内容) のリスト。編集前の内容は、まだないファイルではNone。
    # パッチの移動では移動先を書き込み先とする。
    # 削除するファイルと、編集後の内容を特定できない編集（ツール自体が失敗する）は含めない。
    tool_name, tool_args = _parse_tool_call(tool_name, raw_tool_args)
    if tool_name == 'apply_patch':
        contents = []
        for section in _parse_patches(tool_args):
            if section['operation'] == 'Delete':
                continue
            try:
                before, after = _simulate_patch_section(section, cwd)
            except _UndeterminedContent:
                continue
            target = section['moved_to'] if section['moved_to'] is not None else section['path']
            contents.append((os.path.abspath(os.path.join(cwd, target)), before, after))
        return contents
    if not isinstance(tool_args, dict):
        return []
    command = _get_command(tool_name, tool_args)
    path = get_path_argument(tool_args)
    if command in NON_WRITE_COMMANDS or path is None:
        return []
    path = os.path.abspath(os.path.join(cwd, path))
    try:
        current = _read_current(path)
        return [(path, current, _simulate_command(command, tool_args, current))]
    except _UndeterminedContent:
        return []
