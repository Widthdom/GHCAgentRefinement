import argparse
import fnmatch
import hashlib
import io
import json
import os
import re
import runpy
import sqlite3
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+?)\s*$')
_PATCH_MOVE_PATTERN = re.compile(r'^\*\*\* Move to: (.+?)\s*$')
# キーワードの直後が値の代入（`exclude = [...]`・`"exclude": [...]`・`omit:`）である場合だけ値を読む。
# 値が次の行から始まる場合（INIの継続行・YAMLのリスト）は、その行がインデントされていることを求める。
_ASSIGNMENT_PATTERN = re.compile(r'["\']?[ \t]*[:=][ \t]*(?:\r?\n[ \t]+)?')
_QUOTED_PATTERN = re.compile(r'"((?:\\.|[^"\\\n])*)"|\'((?:\\.|[^\'\\\n])*)\'|`([^`\n]*)`')
_OPENING_BRACKETS = {'[': ']', '(': ')', '{': '}'}
# 正規表現のエスケープ（Jestの `slow\.test\.js`）を外す文字。
_ESCAPED_CHARACTER_PATTERN = re.compile(r'\\([./\-_])')
_MAX_FINDINGS = 3
_GIT_TIMEOUT_SECONDS = 5
_FINDING = '{file} adds the exclusion "{entry}", which matches the tracked file {matched}'
_DENY_REASON = (
    'Edit denied: {findings}. Adding existing code or tests to an ignore or exclusion list hides it from the '
    'test runner, linter, compiler, or coverage instead of fixing it, so the failure disappears while the problem '
    'remains. Fix the failing code or test instead, or keep it visible and report the failure to the user. '
    'If excluding these files is genuinely intended, for example because the user asked for it or they are '
    'generated, retry the same edit and state the reason in the final response; this check denies it only once '
    'per file.'
)


class _UndeterminedContent(Exception):
    # 編集後の内容を特定できない場合（置換前の内容が見つからないなど）。ツール自体が失敗するため検査しない。
    pass


def _compile_patterns(values, key):
    if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError(f'exclusion definitions require an array of non-empty strings for {key}.')
    try:
        return [re.compile(value) for value in values]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {key}: {ex}') from None


def _load_definitions(path):
    # 戻り値は {ignore_files: [正規表現], config_files: [{name, file, values, keywords}]}。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('exclusion definitions must be a JSON object.')
    ignore_files = _compile_patterns(definitions.get('ignoreFiles'), 'ignoreFiles')
    entries = definitions.get('configFiles')
    if not isinstance(entries, list) or not entries:
        raise ValueError('exclusion definitions require a non-empty array for configFiles.')
    config_files = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('name'), str) or not entry['name'].strip():
            raise ValueError('each configFiles entry requires a non-empty name.')
        values = _compile_patterns(entry.get('valuePatterns'), 'valuePatterns')
        if any(pattern.groups != 1 for pattern in values):
            raise ValueError('each valuePatterns entry requires exactly one capturing group.')
        keywords = _compile_patterns(entry.get('keywordPatterns'), 'keywordPatterns')
        if not values and not keywords:
            raise ValueError(f'configFiles entry {entry["name"]} requires valuePatterns or keywordPatterns.')
        config_files.append({
            'name': entry['name'],
            'file': _compile_patterns([entry.get('filePattern')], 'filePattern')[0],
            'values': values,
            'keywords': keywords,
        })
    return {'ignore_files': ignore_files, 'config_files': config_files}


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


def _get_edited_content(tool_name, tool_args, current):
    # 編集ツールの引数から、編集後のファイル全体の内容を求める。
    command = tool_args.get('command')
    if tool_name == 'create' or command == 'create':
        file_text = next((tool_args[key] for key in ('file_text', 'content') if isinstance(tool_args.get(key), str)), None)
        if file_text is None:
            raise _UndeterminedContent()
        return file_text
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


def _get_patch_edits(text, cwd):
    # 戻り値は (書き込み先のパス, 編集前の内容, 編集後の内容) のリスト。削除するファイルは対象外とする。
    files = []
    for line in text.splitlines():
        match = _PATCH_FILE_PATTERN.match(line)
        if match:
            files.append({'kind': match.group(1), 'path': match.group(2), 'target': match.group(2), 'lines': []})
        elif files and (move := _PATCH_MOVE_PATTERN.match(line)):
            files[-1]['target'] = move.group(1)
        elif files and line != '*** End Patch':
            files[-1]['lines'].append(line)
    edits = []
    for file in files:
        if file['kind'] == 'Delete':
            continue
        target = os.path.abspath(os.path.join(cwd, file['target']))
        try:
            if file['kind'] == 'Add':
                before = ''
                after = '\n'.join(line[1:] for line in file['lines'] if line.startswith('+')) + '\n'
            else:
                # 移動を伴う場合も、移動元の内容を編集前とする。
                before = _read_current(os.path.abspath(os.path.join(cwd, file['path'])))
                after = _apply_update(before, file['lines'])
        except _UndeterminedContent:
            continue
        edits.append((target, before or '', after))
    return edits


def _get_edits(tool_name, tool_args, cwd):
    # 戻り値は (書き込み先のパス, 編集前の内容, 編集後の内容) のリスト。
    if tool_name == 'apply_patch':
        texts = [tool_args] if isinstance(tool_args, str) else [
            value for value in (tool_args.values() if isinstance(tool_args, dict) else []) if isinstance(value, str)
        ]
        return [edit for text in texts for edit in _get_patch_edits(text, cwd)]
    if not isinstance(tool_args, dict) or tool_args.get('command') in ('view', 'undo_edit'):
        return []
    path = tool_args.get('path')
    if not isinstance(path, str) or not path.strip():
        return []
    path = os.path.abspath(os.path.join(cwd, path))
    try:
        current = _read_current(path)
        return [(path, current or '', _get_edited_content(tool_name, tool_args, current))]
    except _UndeterminedContent:
        return []


def _take_bracketed(text, start):
    # text[start]の括弧から、対応する閉じ括弧までを返す。引用符の中の括弧は数えない。
    closing_stack = []
    quote = None
    index = start
    while index < len(text):
        character = text[index]
        if quote:
            if character == '\\':
                index += 1
            elif character == quote:
                quote = None
        elif character in '"\'`':
            quote = character
        elif character in _OPENING_BRACKETS:
            closing_stack.append(_OPENING_BRACKETS[character])
        elif closing_stack and character == closing_stack[-1]:
            closing_stack.pop()
            if not closing_stack:
                return text[start:index + 1]
        index += 1
    return text[start:]


def _take_value(text, start):
    # 代入の値を返す。括弧・引用符で始まればその範囲、それ以外は行末までと、続くインデントされた行（INI・YAML）。
    if start >= len(text):
        return ''
    if text[start] in _OPENING_BRACKETS:
        return _take_bracketed(text, start)
    if text[start] in '"\'`':
        match = _QUOTED_PATTERN.match(text, start)
        return match.group(0) if match else ''
    lines = text[start:].splitlines()
    value = [lines[0]]
    for line in lines[1:]:
        if not line.strip() or not line[:1].isspace():
            break
        value.append(line)
    return '\n'.join(value)


def _get_tokens(value):
    # 引用符があれば引用符の中の文字列、なければ空白・カンマ・YAMLの `-` で区切った語を返す。
    quoted = [next(group for group in match.groups() if group is not None) for match in _QUOTED_PATTERN.finditer(value)]
    if quoted:
        return quoted
    return [token for token in re.split(r'[\s,]+', value) if token and token != '-']


def _normalize_entry(entry):
    # 除外の指定を、設定ファイルのディレクトリからの `/` 区切りのパスやglobにそろえる。否定（`!`）や空はNone。
    entry = entry.strip()
    if not entry or entry.startswith(('!', '#')):
        return None
    entry = entry.replace('\\\\', '\\')
    entry = _ESCAPED_CHARACTER_PATTERN.sub(r'\1', entry).replace('\\', '/')
    entry = entry.split('::', 1)[0].removeprefix('<rootDir>').removeprefix('^').removesuffix('$')
    while True:
        stripped = entry.removeprefix('./').removeprefix('/').removeprefix('**/')
        stripped = stripped.removesuffix('/**').removesuffix('/*').removesuffix('/')
        if stripped == entry:
            break
        entry = stripped
    return entry if entry and entry != '.' else None


def _get_exclusions(config, text):
    # 戻り値は、設定に書かれた除外の指定（正規化済み）の集合。
    entries = []
    for pattern in config.get('values', []):
        entries.extend(match.group(1) for match in pattern.finditer(text))
    for pattern in config.get('keywords', []):
        for match in pattern.finditer(text):
            assignment = _ASSIGNMENT_PATTERN.match(text, match.end())
            if assignment:
                entries.extend(_get_tokens(_take_value(text, assignment.end())))
    if config.get('ignore_file'):
        entries.extend(line for line in text.splitlines())
    return {normalized for entry in entries if (normalized := _normalize_entry(entry))}


def _get_config(definitions, path):
    # 戻り値は、パスが対象の設定ファイルなら照合方法、そうでなければNone。
    name = os.path.basename(path)
    if any(pattern.search(name) for pattern in definitions['ignore_files']):
        return {'ignore_file': True}
    return next((config for config in definitions['config_files'] if config['file'].search(name)), None)


def _get_tracked_files(directory):
    # 戻り値は、directory配下のgit管理下のファイルの、directoryからの `/` 区切りの相対パス。
    # gitがない、またはgit管理下でなければNoneを返して検査しない。
    output = _git['get_git_output'](directory, ['ls-files', '-z'], _GIT_TIMEOUT_SECONDS)
    if output is None:
        return None
    return [path for path in output.split('\0') if path]


def _find_matched_file(entry, files):
    # 除外の指定に一致する最初のファイルを返す。globでなければ、パスの先頭・途中のディレクトリやファイル名として照合する。
    is_glob = any(character in entry for character in '*?[')
    # fnmatchの `*` は `/` にも一致する。`**/` が0階層に一致する場合も照合する（`src/**/*.ts` と `src/a.ts`）。
    globs = {entry, entry.replace('**/', '')}
    for file in files:
        if is_glob:
            if (any(fnmatch.fnmatchcase(file, glob) or fnmatch.fnmatchcase(file, glob + '/*') for glob in globs)
                    or ('/' not in entry and any(fnmatch.fnmatchcase(part, entry) for part in file.split('/')))):
                return file
        elif f'/{entry}/' in f'/{file}/':
            return file
    return None


def _record_denial(state_path, path):
    # 戻り値は、このファイルを今回初めて拒否するか。同じファイルの拒否はセッションで1回だけにする。
    # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
    file_key = hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()
    return bool(_filter_first_time_denials(state_path, [(file_key, path)]))


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'exclusion-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-exclusion-escapes', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        tool_name = tool_name.removeprefix('functions.')
        edits = _get_edits(tool_name, _unwrap_tool_args(tool_args), cwd) if tool_name in _EDIT_TOOL_NAMES else []

        candidates = []
        tracked_files_by_directory = {}
        for path, before, after in edits:
            config = _get_config(definitions, path)
            if config is None:
                continue
            added = sorted(_get_exclusions(config, after) - _get_exclusions(config, before))
            if not added:
                continue
            # 除外の指定は、設定ファイルのディレクトリからの相対パスとして照合する。
            directory = os.path.dirname(path)
            if directory not in tracked_files_by_directory:
                tracked_files_by_directory[directory] = _get_tracked_files(directory) if os.path.isdir(directory) else None
            tracked_files = tracked_files_by_directory[directory]
            if not tracked_files:
                continue
            matches = [(entry, matched) for entry in added if (matched := _find_matched_file(entry, tracked_files))]
            if matches:
                candidates.append((path, matches))

        findings = []
        if candidates:
            real_cwd = os.path.realpath(cwd)
            for path, matches in candidates:
                if not _record_denial(state_path, os.path.realpath(path)):
                    continue
                display_path = _get_display_path(os.path.realpath(path), real_cwd)
                directory = os.path.dirname(display_path)
                for entry, matched in matches:
                    matched_path = f'{directory}/{matched}' if directory else matched
                    findings.append(_FINDING.format(file=display_path, entry=entry, matched=matched_path))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check exclusion escapes: {ex}', file=sys.stderr)
        return 2

    if findings:
        shown = findings[:_MAX_FINDINGS]
        if len(findings) > _MAX_FINDINGS:
            shown.append(f'and {len(findings) - _MAX_FINDINGS} more')
        reason = _DENY_REASON.format(findings='; '.join(shown))
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
