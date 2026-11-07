import os
import re
import runpy

# 編集ツールの呼び出し1件から、ファイルごとの変更を取り出す。
# 書き込む内容を検査するpreToolUse hook、編集後のファイルを確かめるpostToolUse hook、
# 依頼中の編集を振り返るagentStop hookが共有する。

# 他のutilityの処理は、スクリプトから読み込む。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']

_EDIT_TOOL_NAMES = frozenset({'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'})
# 書き込む内容ではない引数と、置換前の内容を表す引数。
_IGNORED_KEYS = frozenset({'path', 'command'})
_REMOVED_TEXT_KEYS = ('old_str', 'old_string')
_NEW_TEXT_KEYS = ('new_str', 'new_string')
_FULL_CONTENT_KEYS = ('file_text', 'content')
# 書き込みではないstr_replace_editorのコマンド。undo_editは書き込む内容を引数に持たない。
_NON_WRITE_COMMANDS = frozenset({'view', 'undo_edit'})
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+?)\s*$')


def _get_patch_changes(text):
    # パッチをファイルごとに分け、追加行（`+`）を書き込む内容、削除行（`-`）を置換前の内容とする。
    # 文脈行（` `）を含む本文の行は、行の並びを使うhookのために `patch_lines` に残す。
    changes = []
    for line in text.splitlines():
        match = _PATCH_FILE_PATTERN.match(line)
        if match:
            changes.append({
                'path': match.group(2), 'written': [], 'removed': [], 'deleted': match.group(1) == 'Delete',
                'full_content': None, 'replacement': None, 'patch_lines': [],
            })
        elif changes and line[:1] in ('+', '-', ' '):
            change = changes[-1]
            change['patch_lines'].append((line[0], line[1:]))
            if line[0] == '+':
                change['written'].append(line[1:])
            elif line[0] == '-':
                change['removed'].append(line[1:])
    for change in changes:
        change['written'] = '\n'.join(change['written'])
        change['removed'] = '\n'.join(change['removed'])
    return changes


def _get_first_string(tool_args, keys):
    return next((tool_args[key] for key in keys if isinstance(tool_args.get(key), str)), None)


def extract_edit_tool_changes(tool_name, raw_tool_args, include_deleted=False):
    # 戻り値は次の項目を持つ辞書のリスト。編集ツール以外や、書き込みではない呼び出しでは空。
    #   path：ツールに渡されたファイルのパス。
    #   written：書き込む内容。置換・挿入では新しい内容、パッチでは追加行。
    #   removed：置換前の内容。置換では古い内容、パッチでは削除行。
    #   deleted：パッチによるファイルの削除か。include_deletedがTrueのときだけ結果に含める。
    #   full_content：ファイル全体を書き込む作成（create）の内容。それ以外はNone。
    #   replacement：置換前後の内容の組（古い内容, 新しい内容）。置換以外はNone。
    #   patch_lines：パッチの本文の行（記号, 内容）のリスト。記号は `+`・`-`・` `。パッチ以外はNone。
    tool_name = tool_name.removeprefix('functions.')
    if tool_name not in _EDIT_TOOL_NAMES:
        return []
    tool_args = _unwrap_tool_args(raw_tool_args)
    if tool_name == 'apply_patch':
        changes = [change for text in _collect_json_string_values(tool_args) for change in _get_patch_changes(text)]
        return [change for change in changes if include_deleted or not change['deleted']]
    if not isinstance(tool_args, dict):
        return []
    command = tool_args.get('command')
    if tool_name == 'str_replace_editor' and command in _NON_WRITE_COMMANDS:
        return []
    path = tool_args.get('path')
    if not isinstance(path, str) or not path.strip():
        return []
    is_create = tool_name == 'create' or (tool_name == 'str_replace_editor' and command == 'create')
    old = _get_first_string(tool_args, _REMOVED_TEXT_KEYS)
    new = _get_first_string(tool_args, _NEW_TEXT_KEYS)
    return [{
        'path': path,
        'written': '\n'.join(_collect_json_string_values(tool_args, _IGNORED_KEYS | frozenset(_REMOVED_TEXT_KEYS))),
        'removed': '\n'.join(text for key in _REMOVED_TEXT_KEYS for text in _collect_json_string_values(tool_args.get(key))),
        'deleted': False,
        'full_content': _get_first_string(tool_args, _FULL_CONTENT_KEYS) if is_create else None,
        'replacement': (old, new) if old and new is not None else None,
        'patch_lines': None,
    }]
