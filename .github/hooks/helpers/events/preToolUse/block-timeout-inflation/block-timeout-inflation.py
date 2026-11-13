import argparse
import io
import json
import os
import re
import runpy
import sqlite3
import sys
from collections import Counter
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
_TIMING_PATTERNS_KEY = 'timingPatterns'
# 数値リテラル。`100ms`・`5000L`・`0.5f` のような単位や型の接尾辞は数値に含めず、行の形の側に残す。
# 単位が変わる書き換え（`5m` から `300s`）は行の形が一致しないため、大小を比べない。
_NUMBER_PATTERN = re.compile(r'(?<![\w.])\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?(?=[A-Za-z]*\b)')
_NUMBER_PLACEHOLDER = '\0'
# 空行とコメントだけの行は、ロジックの変更とみなさない。
_NEUTRAL_LINE_PATTERN = re.compile(r'^(?:$|//|#|/\*|\*|--|<!--)')
_MAX_SNIPPETS = 3
_MAX_SNIPPET_LENGTH = 80
_FINDING = '{file} would only raise timing values ({snippets})'
_DENY_REASON = (
    'Edit denied: {findings}. This edit raises timeouts, delays, sleeps, or retry counts without changing any logic. '
    'Waiting longer hides why an operation is slow or flaky instead of fixing it. Find the cause, such as a race, a '
    'missing await, a wrong wait condition, or real slowness, and fix that. If the larger value is genuinely needed, '
    'for example when the user asked for it or the operation is known to take that long, retry the same edit; this '
    'check denies it only once per file.'
)


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('timeout inflation definitions must be a JSON object.')
    loaded = []
    for key, allow_empty in ((_EXCLUDED_FILE_PATTERNS_KEY, True), (_TIMING_PATTERNS_KEY, False)):
        patterns = definitions.get(key)
        if not _is_non_blank_string_list(patterns, allow_empty):
            raise ValueError(f'timeout inflation definitions require an array of non-empty strings for {key}.')
        loaded.append(_build_regex_matchers(patterns, key))
    return loaded[0], loaded[1]


def _get_changed_lines(text, other):
    # textにあってotherにない行を、重複を含めて返す。前後の空白と、空行・コメントだけの行は除く。
    changed = Counter(line.strip() for line in text.splitlines()) - Counter(line.strip() for line in other.splitlines())
    return [line for line in changed.elements() if not _NEUTRAL_LINE_PATTERN.match(line)]


def _split_numbers(line):
    return _NUMBER_PATTERN.sub(_NUMBER_PLACEHOLDER, line), [
        float(number.replace('_', '')) for number in _NUMBER_PATTERN.findall(line)
    ]


def _is_inflated(old_numbers, new_numbers):
    # 同じ位置の数値が1つ以上増え、減ったものがない。
    return any(new > old for old, new in zip(old_numbers, new_numbers)) and all(
        new >= old for old, new in zip(old_numbers, new_numbers)
    )


def _find_inflation(timing_patterns, removed, written):
    # 書き換えた行がすべて、待ち時間・再試行の数値を増やすだけの書き換えなら、(書き換え前, 書き換え後) の行の組を返す。
    # ほかの変更（ロジックの変更、行の追加・削除）を1つでも含む場合は空のリストを返す。
    old_lines = _get_changed_lines(removed, written)
    new_lines = _get_changed_lines(written, removed)
    if not new_lines or len(old_lines) != len(new_lines):
        return []
    pairs = []
    for new_line in new_lines:
        if not any(pattern.search(new_line) for pattern in timing_patterns):
            return []
        new_skeleton, new_numbers = _split_numbers(new_line)
        match = next((
            old_line for old_line in old_lines
            if _split_numbers(old_line)[0] == new_skeleton and _is_inflated(_split_numbers(old_line)[1], new_numbers)
        ), None)
        if match is None:
            return []
        old_lines.remove(match)
        pairs.append((match, new_line))
    return pairs


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'timeout-inflation-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, timing_patterns = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-timeout-inflation', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        real_cwd = os.path.realpath(cwd)
        candidates = []
        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        for change in extract_edit_tool_changes(tool_name, tool_args):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = _get_display_path(path, real_cwd)
            # パターンは、get-display-pathがOSによらず `/` 区切りで返すパスに対して照合する。
            if any(pattern.search(display_path) for pattern in excluded_file_patterns):
                continue
            # 上書きではファイル全体、それ以外では置換前の内容と比べる。
            current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
            if change['full_content'] is not None and current:
                removed, written = current, change['full_content']
            else:
                removed, written = change['removed'], change['written']
            pairs = _find_inflation(timing_patterns, removed, written)
            if pairs:
                # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                file_key = _get_path_sha256_digest(path)
                candidates.append((file_key, _FINDING.format(file=display_path, snippets=', '.join(
                    f'"{_truncate_text(old, _MAX_SNIPPET_LENGTH)}" -> "{_truncate_text(new, _MAX_SNIPPET_LENGTH)}"'
                    for old, new in pairs[:_MAX_SNIPPETS]
                ))))

        reasons = []
        if candidates:
            reasons = _filter_first_time_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check timeout inflation: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings='; '.join(reasons))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
