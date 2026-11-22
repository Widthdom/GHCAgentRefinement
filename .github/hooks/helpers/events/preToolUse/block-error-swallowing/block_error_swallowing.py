import argparse
import io
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
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read_hook_input.py'))
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write_hook_output.py'))
_truncate_text = runpy.run_path(os.path.join(_utilities_directory, 'truncate-text', 'truncate_text.py'))['truncate_text']
_get_path_sha256_digest = runpy.run_path(os.path.join(
    _utilities_directory, 'get-sha256-digest', 'get_sha256_digest.py',
))['get_path_sha256_digest']
_definitions = runpy.run_path(os.path.join(_utilities_directory, 'read-definitions', 'read_definitions.py'))
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get_state_path.py'))['get_state_path']
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter_first_time_denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get_display_path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read_stored_file_content_for_write_comparison.py',
))['read_stored_file_content_for_write_comparison']


_EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
_SWALLOWING_PATTERNS_KEY = 'swallowingPatterns'
_MAX_SNIPPETS = 3
_MAX_SNIPPET_LENGTH = 80
_FINDING = '{file} would get {kinds} ({snippets})'
_DENY_REASON = (
    'Edit denied: {findings}. Silencing an error or a diagnostic makes the symptom disappear without fixing the cause, '
    'and hides the next failure too. Fix the cause, or handle the error explicitly: log it, re-raise it, or return it '
    'to the caller. If ignoring it is intended, for example best-effort cleanup or when the user asked for it, '
    'add a comment explaining why and retry the same edit; this check denies it only once per file.'
)


def _load_definitions(path):
    label = 'error swallowing definitions'
    definitions = _definitions['read_definitions'](path, label)
    # 行全体に一致させる定義のため、行頭・行末は各行に一致させる。
    return (
        _definitions['get_regex_matchers'](definitions, _EXCLUDED_FILE_PATTERNS_KEY, label, allow_empty_list=True),
        _definitions['get_named_regex_matchers'](definitions, _SWALLOWING_PATTERNS_KEY, label, flags=re.MULTILINE),
    )


def _normalize_lines(text):
    # インデントの違いで既存の内容との照合が外れないよう、各行の前後の空白を除く。
    return '\n'.join(line.strip() for line in text.splitlines())


def _find_swallowing(swallowing_patterns, written, existing):
    # 戻り値は (種類の名前のリスト, 一致した箇所のリスト)。既存の内容に同じ箇所があるものは、元からあるものとみなす。
    normalized_existing = _normalize_lines(existing)
    kinds = []
    snippets = []
    for name, pattern in swallowing_patterns:
        for match in pattern.finditer(written):
            snippet = _normalize_lines(match.group())
            if not snippet or snippet in normalized_existing or snippet in snippets:
                continue
            snippets.append(snippet)
            if name not in kinds:
                kinds.append(name)
    return kinds, snippets


def _format_snippet(snippet):
    snippet = snippet.replace('\n', ' ')
    return f'"{_truncate_text(snippet, _MAX_SNIPPET_LENGTH)}"'


def main(arguments=None):
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'error-swallowing-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, swallowing_patterns = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-error-swallowing', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        real_cwd = os.path.realpath(cwd)
        candidates = []
        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'edit-tool-changes', 'edit_tool_changes.py',
        ))['extract_edit_tool_changes']
        for change in extract_edit_tool_changes(tool_name, tool_args):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = _get_display_path(path, real_cwd)
            # パターンは、get-display-pathがOSによらず `/` 区切りで返すパスに対して照合する。
            if any(pattern.search(display_path) for pattern in excluded_file_patterns):
                continue
            current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
            # 置換を適用できればファイル全体を、できなければ書き込む内容を、それぞれ既存の内容と比べる。
            replacement = change['replacement']
            if replacement and replacement[0] in current:
                written, existing = current.replace(*replacement, 1), current
            else:
                written, existing = change['written'], '\n'.join([change['removed'], current])
            kinds, snippets = _find_swallowing(swallowing_patterns, written, existing)
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
        print(f'Failed to check error swallowing: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings='; '.join(reasons))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
