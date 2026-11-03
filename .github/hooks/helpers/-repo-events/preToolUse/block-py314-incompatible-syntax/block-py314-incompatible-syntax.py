import ast
import io
import json
import os
import re
import sys
import warnings
from typing import cast


# 判定の基準とするPythonのバージョン。これより新しい文法も、古い文法（Python 2の構文など）も拒否する。
_TARGET_VERSION = (3, 14)
# Pythonのソースとして扱う拡張子。
_PYTHON_EXTENSIONS = {'.py', '.pyi', '.pyw'}
# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+?)\s*$')
_PATCH_MOVE_PATTERN = re.compile(r'^\*\*\* Move to: (.+?)\s*$')
_MAX_FINDINGS = 3
_FINDING = '{file} line {line}: {message}'
_DENY_REASON = (
    'Edit denied: the resulting Python source would not compile on Python 3.14 ({findings}). This repository '
    'targets Python 3.14, so do not use syntax that 3.14 rejects, such as syntax added in later versions or '
    'removed Python 2 syntax. Fix the code so the whole file compiles on Python 3.14 and retry.'
)


class _UndeterminedContent(Exception):
    # 編集後の内容を特定できない場合（置換前の内容が見つからないなど）。ツール自体が失敗するため検査しない。
    pass


def _get_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列（apply_patch）で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


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


def _get_edited_content(tool_name, tool_args, path):
    # 編集ツールの引数から、編集後のファイル全体の内容を求める。
    command = tool_args.get('command')
    if tool_name == 'create' or command == 'create':
        file_text = tool_args.get('file_text')
        if not isinstance(file_text, str):
            raise _UndeterminedContent()
        return file_text
    current = _read_current(path)
    if tool_name == 'insert' or command == 'insert':
        return _insert_after_line(current, tool_args.get('insert_line'), tool_args.get('new_str'))
    return _replace_once(current, tool_args.get('old_str'), tool_args.get('new_str'))


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
    # 戻り値は (書き込み先のパス, 編集後の内容) のリスト。削除するファイルは対象外とする。
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
        target = os.path.abspath(os.path.join(cwd, file['target']))
        if file['kind'] == 'Delete' or not _is_python_path(target):
            continue
        try:
            if file['kind'] == 'Add':
                content = '\n'.join(line[1:] for line in file['lines'] if line.startswith('+')) + '\n'
            else:
                source = os.path.abspath(os.path.join(cwd, file['path']))
                content = _apply_update(_read_current(source), file['lines'])
        except _UndeterminedContent:
            continue
        contents.append((target, content))
    return contents


def _get_contents(tool_name, tool_args, cwd):
    # 戻り値は (書き込み先のパス, 編集後の内容) のリスト。
    if tool_name == 'apply_patch':
        texts = [tool_args] if isinstance(tool_args, str) else [
            value for value in (tool_args.values() if isinstance(tool_args, dict) else []) if isinstance(value, str)
        ]
        return [content for text in texts for content in _get_patch_contents(text, cwd)]
    if not isinstance(tool_args, dict) or tool_args.get('command') in ('view', 'undo_edit'):
        return []
    path = tool_args.get('path')
    if not isinstance(path, str) or not path.strip():
        return []
    path = os.path.abspath(os.path.join(cwd, path))
    if not _is_python_path(path):
        return []
    try:
        return [(path, _get_edited_content(tool_name, tool_args, path))]
    except _UndeterminedContent:
        return []


def _is_python_path(path):
    return os.path.splitext(path)[1].lower() in _PYTHON_EXTENSIONS


def _get_syntax_error(source, path):
    # 構文解析に加え、コンパイルで検出される誤り（関数外のreturnなど）も対象とする。警告は対象外。
    try:
        with warnings.catch_warnings(action='ignore'):
            tree = ast.parse(source, path, feature_version=_TARGET_VERSION)
            compile(tree, path, 'exec', dont_inherit=True)
    except SyntaxError as ex:
        return ex
    return None


def _get_display_path(path, cwd):
    # cwd配下のファイルは、OSによらず `/` 区切りの相対パスで示す。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path.replace(os.sep, '/')


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd must be a non-empty string.')
        if not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        # preToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        findings = []
        tool_name = tool_name.removeprefix('functions.')
        if tool_name in _EDIT_TOOL_NAMES:
            cwd = os.path.abspath(cwd)
            for path, content in _get_contents(tool_name, _get_tool_args(hook_input['toolArgs']), cwd):
                # 実行中のPythonが基準より古いと、基準の文法を誤って拒否するため判定しない。
                if sys.version_info < _TARGET_VERSION:
                    raise ValueError(f'Python {_TARGET_VERSION[0]}.{_TARGET_VERSION[1]} or later is required.')
                error = _get_syntax_error(content, path)
                if error is None:
                    continue
                # 既にコンパイルできないファイルの編集は、修正途中の場合もあるため拒否しない。
                try:
                    current = _read_current(path)
                except _UndeterminedContent:
                    current = None
                if current is not None and _get_syntax_error(current, path) is not None:
                    continue
                findings.append(_FINDING.format(
                    file=_get_display_path(path, cwd), line=error.lineno, message=error.msg,
                ))
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check Python 3.14 syntax: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = _DENY_REASON.format(findings='; '.join(findings[:_MAX_FINDINGS]))
        print(reason, file=sys.stderr)
        # SDK形式のトップレベルpermissionDecision。正常な拒否なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({
            'permissionDecision': 'deny',
            'permissionDecisionReason': reason,
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
