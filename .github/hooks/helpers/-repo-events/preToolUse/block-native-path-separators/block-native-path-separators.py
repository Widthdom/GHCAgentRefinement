import argparse
import io
import json
import os
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
_truncate_text = runpy.run_path(os.path.join(_utilities_directory, 'truncate-text', 'truncate-text.py'))['truncate_text']
_get_path_sha256_digest = runpy.run_path(os.path.join(
    _utilities_directory, 'get-sha256-digest', 'get-sha256-digest.py',
))['get_path_sha256_digest']
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_extract_edit_tool_changes = runpy.run_path(os.path.join(
    _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
))['extract_edit_tool_changes']
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']


_EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
_NATIVE_PATH_PATTERNS_KEY = 'nativePathPatterns'
_NORMALIZATION_PATTERNS_KEY = 'normalizationPatterns'
_MAX_SNIPPETS = 3
_MAX_SNIPPET_LENGTH = 80
_FINDING = '{file} would get {kinds} without converting to "/" separators ({snippets})'
_DENY_REASON = (
    'Edit denied: {findings}. These calls return paths with the OS-native separator, which is "\\" on Windows and "/" '
    'elsewhere, so code and tests that pass on one OS fail on another once the path is shown, stored, or compared as '
    'a string. Convert the path to "/" separators where it leaves file system APIs (for example '
    '`.replace(os.sep, \'/\')`, `.as_posix()`, or `filepath.ToSlash`), and make tests expect the same form. If the '
    'path is only passed back to file system APIs or is converted on another line, retry the same edit; this check '
    'denies it only once per file.'
)


def _is_string_list(value, allow_empty):
    return isinstance(value, list) and (allow_empty or bool(value)) and all(
        isinstance(item, str) and item.strip() for item in value
    )


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('path separator definitions must be a JSON object.')
    if not _is_string_list(definitions.get(_EXCLUDED_FILE_PATTERNS_KEY), allow_empty=True):
        raise ValueError(
            f'path separator definitions require an array of non-empty strings for {_EXCLUDED_FILE_PATTERNS_KEY}.'
        )
    if not _is_string_list(definitions.get(_NORMALIZATION_PATTERNS_KEY), allow_empty=False):
        raise ValueError(
            f'path separator definitions require a non-empty array of non-empty strings for '
            f'{_NORMALIZATION_PATTERNS_KEY}.'
        )
    native_definitions = definitions.get(_NATIVE_PATH_PATTERNS_KEY)
    if not isinstance(native_definitions, list) or not native_definitions or not all(
        isinstance(definition, dict) and all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        )
        for definition in native_definitions
    ):
        raise ValueError(
            f'path separator definitions require a non-empty array of objects with non-empty strings for name and '
            f'pattern in {_NATIVE_PATH_PATTERNS_KEY}.'
        )
    native_patterns = _build_regex_matchers(
        [definition['pattern'] for definition in native_definitions], _NATIVE_PATH_PATTERNS_KEY,
    )
    return (
        _build_regex_matchers(definitions[_EXCLUDED_FILE_PATTERNS_KEY], _EXCLUDED_FILE_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(native_definitions, native_patterns)],
        _build_regex_matchers(definitions[_NORMALIZATION_PATTERNS_KEY], _NORMALIZATION_PATTERNS_KEY),
    )


def _normalize_lines(text):
    # インデントの違いで既存の内容との照合が外れないよう、各行の前後の空白を除く。
    return {line.strip() for line in text.splitlines()}


def _find_native_paths(native_patterns, normalization_patterns, written, existing):
    # 戻り値は (種類の名前のリスト, 該当行のリスト)。
    # 同じ行で `/` 区切りに変換しているものと、既存の内容に同じ行があるもの（元からあるもの）は除く。
    existing_lines = _normalize_lines(existing)
    kinds = []
    snippets = []
    for line in written.splitlines():
        line = line.strip()
        if not line or line in existing_lines or line in snippets:
            continue
        if any(pattern.search(line) for pattern in normalization_patterns):
            continue
        names = [name for name, pattern in native_patterns if pattern.search(line)]
        if names:
            snippets.append(line)
            kinds.extend(name for name in names if name not in kinds)
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
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'path-separator-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, native_patterns, normalization_patterns = _load_definitions(
            parsed_arguments.definitions_path
        )

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-native-path-separators', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        real_cwd = os.path.realpath(cwd)
        candidates = []
        for change in _extract_edit_tool_changes(tool_name, tool_args):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = _get_display_path(path, real_cwd)
            if any(pattern.search(display_path) for pattern in excluded_file_patterns):
                continue
            current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
            kinds, snippets = _find_native_paths(
                native_patterns, normalization_patterns, change['written'], '\n'.join([change['removed'], current]),
            )
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
        print(f'Failed to check native path separators: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings='; '.join(reasons))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
