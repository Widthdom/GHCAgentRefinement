import argparse
import io
import os
import sqlite3
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read-hook-input'),
    os.path.join(_utilities_directory, 'write-hook-output'),
    os.path.join(_utilities_directory, 'truncate-text'),
    os.path.join(_utilities_directory, 'get-sha256-digest'),
    os.path.join(_utilities_directory, 'read-definitions'),
    os.path.join(_utilities_directory, 'get-state-path'),
    os.path.join(_utilities_directory, 'edit-tool-changes'),
    os.path.join(_utilities_directory, 'filter-first-time-denials'),
    os.path.join(_utilities_directory, 'get-display-path'),
    os.path.join(_utilities_directory, 'read-stored-file-content-for-write-comparison'),
    os.path.join(_utilities_directory, 'get-stripped-lines'),
]
import read_hook_input as _hook_input
import write_hook_output as _hook_output
from truncate_text import truncate_text as _truncate_text
from get_sha256_digest import get_path_sha256_digest as _get_path_sha256_digest
import read_definitions as _definitions
from get_state_path import get_state_path as _get_state_path
from edit_tool_changes import extract_edit_tool_changes as _extract_edit_tool_changes
from filter_first_time_denials import filter_first_time_denials as _filter_first_time_denials
from get_display_path import get_display_path as _get_display_path
from read_stored_file_content_for_write_comparison import read_stored_file_content_for_write_comparison as _read_stored_file_content_for_write_comparison
from get_stripped_lines import get_stripped_lines as _get_stripped_lines


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


def _load_definitions(path):
    label = 'path separator definitions'
    definitions = _definitions.read_definitions(path, label)
    return (
        _definitions.get_regex_matchers(definitions, _EXCLUDED_FILE_PATTERNS_KEY, label, allow_empty_list=True),
        _definitions.get_named_regex_matchers(definitions, _NATIVE_PATH_PATTERNS_KEY, label),
        _definitions.get_regex_matchers(definitions, _NORMALIZATION_PATTERNS_KEY, label),
    )


def _find_native_paths(native_patterns, normalization_patterns, written, existing):
    # 戻り値は (種類の名前のリスト, 該当行のリスト)。
    # 同じ行で `/` 区切りに変換しているものと、既存の内容に同じ行があるもの（元からあるもの）は除く。
    # インデントの違いで既存の内容との照合が外れないよう、各行は前後の空白を除いて比べる。
    existing_lines = _get_stripped_lines(existing)
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
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
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

        hook_input = _hook_input.read_hook_input()
        state_path = _get_state_path('block-native-path-separators', hook_input.get('sessionId'))
        cwd = _hook_input.get_cwd(hook_input)
        tool_name, tool_args = _hook_input.get_tool_call(hook_input)

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
        _hook_output.write_deny_decision(reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
