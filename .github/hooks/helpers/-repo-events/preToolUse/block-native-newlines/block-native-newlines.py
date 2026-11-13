import argparse
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
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_is_non_blank_string_list = runpy.run_path(os.path.join(
    _utilities_directory, 'check-string-list', 'check-string-list.py',
))['is_non_blank_string_list']
_truncate_text = runpy.run_path(os.path.join(_utilities_directory, 'truncate-text', 'truncate-text.py'))['truncate_text']
_get_path_sha256_digest = runpy.run_path(os.path.join(
    _utilities_directory, 'get-sha256-digest', 'get-sha256-digest.py',
))['get_path_sha256_digest']
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']


_INCLUDED_FILE_PATTERNS_KEY = 'includedFilePatterns'
_TRANSLATING_CALLS_KEY = 'translatingCalls'
_TEXT_WRITE_MODE_PATTERN_KEY = 'textWriteModePattern'
_PINNED_NEWLINE_PATTERN_KEY = 'pinnedNewlinePattern'
# 文字列リテラル。三重引用符を先に照合し、`"""` を空の文字列と次の文字列に分けて読まないようにする。
# group(2)は中身。接頭辞（`r`・`b`・`f` など）の後の引用符の位置から照合する。
_STRING_LITERAL_PATTERN = re.compile(
    r'''("""|\'\'\'|"|')((?:\\.|(?!\1).)*)\1''',
    re.DOTALL,
)
_MAX_SNIPPETS = 3
_MAX_SNIPPET_LENGTH = 80
_FINDING = '{file} would get {kinds} without newline=\'\\n\' ({snippets})'
_DENY_REASON = (
    'Edit denied: {findings}. Text streams translate "\\n" to the OS line separator when writing, which is "\\r\\n" '
    'on Windows, so output and files that match on Linux and macOS differ on Windows and the tests fail there. '
    'Pass newline=\'\\n\' to the call (for example `reconfigure(encoding=\'utf-8\', errors=\'replace\', '
    'newline=\'\\n\')` or `open(path, \'w\', encoding=\'utf-8\', newline=\'\\n\')`), or write bytes instead. '
    'If the call never writes text or passes newline on purpose in another way, retry the same edit; this check '
    'denies it only once per file.'
)


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    # 戻り値は (対象のファイルの正規表現のリスト, (名前, 呼び出しの正規表現, 書き込みのモードを要するか) のリスト,
    # 書き込みのモードの正規表現, newlineの指定の正規表現)。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('newline definitions must be a JSON object.')
    included = definitions.get(_INCLUDED_FILE_PATTERNS_KEY)
    if not _is_non_blank_string_list(included):
        raise ValueError(f'newline definitions require a non-empty array of non-empty strings for {_INCLUDED_FILE_PATTERNS_KEY}.')
    calls = definitions.get(_TRANSLATING_CALLS_KEY)
    if not isinstance(calls, list) or not calls or not all(
        isinstance(call, dict) and all(isinstance(call.get(key), str) and call[key].strip() for key in ('name', 'pattern'))
        and isinstance(call.get('requiresTextWriteMode'), bool)
        for call in calls
    ):
        raise ValueError(
            f'newline definitions require a non-empty array of objects with non-empty strings for name and pattern '
            f'and a boolean requiresTextWriteMode in {_TRANSLATING_CALLS_KEY}.'
        )
    for key in (_TEXT_WRITE_MODE_PATTERN_KEY, _PINNED_NEWLINE_PATTERN_KEY):
        if not isinstance(definitions.get(key), str) or not definitions[key].strip():
            raise ValueError(f'newline definitions require a non-empty string for {key}.')
    return (
        _build_regex_matchers(included, _INCLUDED_FILE_PATTERNS_KEY),
        [
            (call['name'], pattern, call['requiresTextWriteMode'])
            for call, pattern in zip(calls, _build_regex_matchers([call['pattern'] for call in calls], _TRANSLATING_CALLS_KEY))
        ],
        _build_regex_matchers([definitions[_TEXT_WRITE_MODE_PATTERN_KEY]], _TEXT_WRITE_MODE_PATTERN_KEY)[0],
        _build_regex_matchers([definitions[_PINNED_NEWLINE_PATTERN_KEY]], _PINNED_NEWLINE_PATTERN_KEY)[0],
    )


def _get_arguments(text, open_index):
    # `(` の位置から対応する `)` までの引数の文字列を返す。文字列の中の括弧は数えない。
    # 書き込む内容が呼び出しの途中で終わる場合は、終わりまでを返す。
    depth = 0
    index = open_index
    while index < len(text):
        character = text[index]
        if character in '"\'':
            literal = _STRING_LITERAL_PATTERN.match(text, index)
            if literal:
                index = literal.end()
                continue
        elif character == '#':
            end = text.find('\n', index)
            index = len(text) if end < 0 else end
            continue
        elif character in '([{':
            depth += 1
        elif character in ')]}':
            depth -= 1
            if depth == 0:
                return text[open_index + 1:index]
        index += 1
    return text[open_index + 1:]


def _get_code_ranges(text):
    # 文字列リテラルとコメントの外側（コード）の範囲を (開始, 終了) のリストで返す。
    # テストの入力などとして文字列に書いた呼び出しを、呼び出しとみなさないために使う。
    ranges = []
    start = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character in '"\'':
            literal = _STRING_LITERAL_PATTERN.match(text, index)
            end = literal.end() if literal else len(text)
        elif character == '#':
            newline = text.find('\n', index)
            end = len(text) if newline < 0 else newline
        else:
            index += 1
            continue
        ranges.append((start, index))
        start = index = end
    ranges.append((start, len(text)))
    return ranges


def _get_line(text, position):
    start = text.rfind('\n', 0, position) + 1
    end = text.find('\n', position)
    return text[start:None if end < 0 else end].strip()


def _find_unpinned_calls(definitions, written, existing):
    # 戻り値は (種類の名前のリスト, 呼び出しの先頭の行のリスト)。
    # newlineを指定した呼び出しと、既存の内容に同じ行がある呼び出し（元からあるもの）は除く。
    _, calls, text_write_mode, pinned_newline = definitions
    existing_lines = {line.strip() for line in existing.splitlines()}
    code_ranges = _get_code_ranges(written)
    kinds = []
    snippets = []
    for name, pattern, requires_text_write_mode in calls:
        for match in pattern.finditer(written):
            if not any(start <= match.start() < end for start, end in code_ranges):
                continue
            line = _get_line(written, match.start())
            if line in existing_lines:
                continue
            arguments = _get_arguments(written, match.end() - 1)
            if pinned_newline.search(arguments):
                continue
            if requires_text_write_mode and not any(
                text_write_mode.match(literal.group(2)) for literal in _STRING_LITERAL_PATTERN.finditer(arguments)
            ):
                continue
            if line not in snippets:
                snippets.append(line)
            if name not in kinds:
                kinds.append(name)
    return kinds, snippets


def _format_snippet(snippet):
    return f'"{_truncate_text(snippet, _MAX_SNIPPET_LENGTH)}"'


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'newline-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = _load_definitions(parsed_arguments.definitions_path)
        extract_edit_tool_changes = runpy.run_path(
            os.path.join(_utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py')
        )['extract_edit_tool_changes']

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-native-newlines', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        real_cwd = os.path.realpath(cwd)
        candidates = []
        for change in extract_edit_tool_changes(tool_name, tool_args):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = _get_display_path(path, real_cwd)
            if not any(pattern.search(display_path) for pattern in definitions[0]):
                continue
            current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
            written = change['full_content'] if change['full_content'] is not None else change['written']
            kinds, snippets = _find_unpinned_calls(definitions, written, '\n'.join([change['removed'], current]))
            if snippets:
                # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                file_key = _get_path_sha256_digest(path)
                candidates.append((file_key, _FINDING.format(
                    file=display_path, kinds=', '.join(kinds),
                    snippets=', '.join(_format_snippet(snippet) for snippet in snippets[:_MAX_SNIPPETS]),
                )))

        reasons = []
        if candidates:
            reasons = _filter_first_time_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check native newlines: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings='; '.join(reasons))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
