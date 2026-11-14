import argparse
import io
import json
import os
import runpy
import sqlite3
import sys
import unicodedata
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


_EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
_CHARACTER_PATTERNS_KEY = 'characterPatterns'
# 既存の行と照合するときに行の前後から除く文字。`str.strip()` は全角スペースやノーブレークスペースも除くため、
# 行頭・行末の全角スペースだけを加えた編集を既存の行とみなさないよう、ASCIIの空白だけを除く。
_LINE_TRIM_CHARACTERS = ' \t\r'
_MAX_SNIPPETS = 3
_MAX_SNIPPET_LENGTH = 80
_FINDING = '{file} would get {kinds} ({snippets})'
_DENY_REASON = (
    'Edit denied: {findings}. These characters are invisible or look like ordinary spaces, so they slip past review '
    'and can break parsing, string comparison, indentation, or change how code reads (bidirectional controls can '
    'reorder what is displayed). Remove them, or replace them with ordinary spaces. If one is intended, for example '
    'test data or a deliberate string, write it by its code point so it is visible, such as chr(0x3000) in Python or '
    'String.fromCodePoint(0x3000) in JavaScript. If you write it as an escape sequence instead, read the file after '
    'the edit and confirm that the escape was not turned into the character itself. Retry this edit unchanged only if '
    'neither removing nor rewriting the character is appropriate; this check denies it only once per file.'
)


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('invisible character definitions must be a JSON object.')
    excluded = definitions.get(_EXCLUDED_FILE_PATTERNS_KEY)
    if not _is_non_blank_string_list(excluded, allow_empty_list=True):
        raise ValueError(
            f'invisible character definitions require an array of non-blank strings for {_EXCLUDED_FILE_PATTERNS_KEY}.'
        )
    character_definitions = definitions.get(_CHARACTER_PATTERNS_KEY)
    if not isinstance(character_definitions, list) or not character_definitions or not all(
        isinstance(definition, dict) and all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        )
        for definition in character_definitions
    ):
        raise ValueError(
            'invisible character definitions require a non-empty array of objects with non-blank strings for name '
            f'and pattern in {_CHARACTER_PATTERNS_KEY}.'
        )
    character_patterns = _build_regex_matchers(
        [definition['pattern'] for definition in character_definitions], _CHARACTER_PATTERNS_KEY,
    )
    return (
        _build_regex_matchers(excluded, _EXCLUDED_FILE_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(character_definitions, character_patterns)],
    )


def _make_visible(text):
    # 理由で示す行の、見えない文字とASCII以外の空白を `<U+XXXX>` に置き換える。
    return ''.join(
        f'<U+{ord(character):04X}>'
        if unicodedata.category(character) == 'Cf' or (unicodedata.category(character) == 'Zs' and character != ' ')
        else character
        for character in text
    )


def _find_invisible_characters(character_patterns, written, existing):
    # 戻り値は (種類の名前のリスト, 該当行のリスト)。既存の内容に同じ行があるものは、元からあるものとみなす。
    existing_lines = {line.strip(_LINE_TRIM_CHARACTERS) for line in existing.splitlines()}
    kinds = []
    lines = []
    for line in written.splitlines():
        matched = [name for name, pattern in character_patterns if pattern.search(line)]
        trimmed = line.strip(_LINE_TRIM_CHARACTERS)
        if not matched or trimmed in existing_lines:
            continue
        if trimmed not in lines:
            lines.append(trimmed)
        kinds.extend(name for name in matched if name not in kinds)
    return kinds, lines


def _format_snippet(line):
    return f'"{_truncate_text(_make_visible(line), _MAX_SNIPPET_LENGTH)}"'


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'invisible-character-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, character_patterns = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-invisible-characters', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        real_cwd = os.path.realpath(cwd)
        candidates = []
        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'edit-tool-changes', 'edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        for change in extract_edit_tool_changes(tool_name, tool_args):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = _get_display_path(path, real_cwd)
            # パターンは、get-display-pathがOSによらず `/` 区切りで返すパスに対して照合する。
            if any(pattern.search(display_path) for pattern in excluded_file_patterns):
                continue
            current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
            kinds, lines = _find_invisible_characters(
                character_patterns, change['written'], '\n'.join([change['removed'], current]),
            )
            if lines:
                # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                file_key = _get_path_sha256_digest(path)
                candidates.append((file_key, _FINDING.format(
                    file=display_path, kinds=', '.join(kinds),
                    snippets=', '.join(_format_snippet(line) for line in lines[:_MAX_SNIPPETS]),
                )))

        reasons = []
        if candidates:
            reasons = _filter_first_time_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check invisible characters: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings='; '.join(reasons))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
