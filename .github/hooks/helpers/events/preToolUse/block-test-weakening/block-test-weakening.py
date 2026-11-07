import argparse
import hashlib
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
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']


_TEST_FILE_PATTERNS_KEY = 'testFilePatterns'
_ASSERTION_PATTERNS_KEY = 'assertionPatterns'
_WEAKENING_PATTERNS_KEY = 'weakeningPatterns'
_WEAKENING = 'weakening'
_ASSERTION_REDUCTION = 'assertion-reduction'
_WEAKENING_REASON = '{file} would get lines that skip, focus, or trivially pass tests ({lines}).'
_ASSERTION_REDUCTION_REASON = '{file} would lose assertions ({old} assertion lines would become {new}).'
_DENY_REASON = (
    'Edit denied: {findings} Weakening a test to make it pass hides the failure instead of fixing it. '
    'Fix the implementation, or keep the test as it is and report the failure to the user. '
    'If the change is intended, for example when the user asked for it or you are only moving or merging '
    'assertions, retry the same edit; this check denies it only once.'
)


def _is_non_empty_string_list(value):
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('test weakening definitions must be a JSON object.')
    for key in (_TEST_FILE_PATTERNS_KEY, _ASSERTION_PATTERNS_KEY):
        if not _is_non_empty_string_list(definitions.get(key)):
            raise ValueError(f'test weakening definitions require a non-empty array of non-empty strings for {key}.')
    weakening_definitions = definitions.get(_WEAKENING_PATTERNS_KEY)
    if not isinstance(weakening_definitions, list) or not weakening_definitions or not all(
        isinstance(definition, dict) and all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        )
        for definition in weakening_definitions
    ):
        raise ValueError(
            f'test weakening definitions require a non-empty array of objects with non-empty strings for name and '
            f'pattern in {_WEAKENING_PATTERNS_KEY}.'
        )
    weakening_patterns = _build_regex_matchers(
        [definition['pattern'] for definition in weakening_definitions], _WEAKENING_PATTERNS_KEY,
    )
    return (
        _build_regex_matchers(definitions[_TEST_FILE_PATTERNS_KEY], _TEST_FILE_PATTERNS_KEY),
        _build_regex_matchers(definitions[_ASSERTION_PATTERNS_KEY], _ASSERTION_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(weakening_definitions, weakening_patterns)],
    )


def _is_test_file(test_file_patterns, display_path):
    # パターンは、get-display-pathがOSによらず `/` 区切りで返すパスに対して照合する。
    return any(pattern.search(display_path) for pattern in test_file_patterns)


def _find_weakening_lines(weakening_patterns, written, existing):
    # 既存の内容に同じ行があるものは、元からあるものとみなす。
    existing_lines = {line.strip() for line in existing.splitlines()}
    lines = []
    for line in written.splitlines():
        stripped = line.strip()
        if stripped in existing_lines or stripped in lines:
            continue
        if any(pattern.search(line) for _, pattern in weakening_patterns):
            lines.append(stripped)
    return lines


def _count_assertion_lines(assertion_patterns, text):
    # 1行に複数のパターンが一致しても1件と数え、パターンの重なりで数がぶれないようにする。
    return sum(1 for line in text.splitlines() if any(pattern.search(line) for pattern in assertion_patterns))


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test-weakening-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        test_file_patterns, assertion_patterns, weakening_patterns = _load_definitions(
            parsed_arguments.definitions_path,
        )

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-test-weakening', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        real_cwd = os.path.realpath(cwd)
        # (拒否のキー, 理由) のリスト。同じファイルでも種類の異なる拒否は、それぞれ1回ずつ行う。
        candidates = []
        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        for change in extract_edit_tool_changes(tool_name, tool_args, include_deleted=True):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            # テストファイルの判定は、Windowsのnormcaseで小文字にする前のパスで行い、大文字小文字を区別できるようにする。
            display_path = _get_display_path(path, real_cwd)
            if not _is_test_file(test_file_patterns, display_path):
                continue
            # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
            file_key = hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()
            current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''

            lines = _find_weakening_lines(weakening_patterns, change['written'], '\n'.join([change['removed'], current]))
            if lines:
                candidates.append((f'{file_key}:{_WEAKENING}', _WEAKENING_REASON.format(
                    file=display_path, lines=', '.join(f'"{line}"' for line in lines[:3]),
                )))

            # 上書きと削除ではファイル全体、それ以外では置換前の内容と比べる。
            if change['deleted']:
                removed, written = current, ''
            elif change['full_content'] is not None and current:
                removed, written = current, change['full_content']
            else:
                removed, written = change['removed'], change['written']
            old_count = _count_assertion_lines(assertion_patterns, removed)
            new_count = _count_assertion_lines(assertion_patterns, written)
            if new_count < old_count:
                candidates.append((f'{file_key}:{_ASSERTION_REDUCTION}', _ASSERTION_REDUCTION_REASON.format(
                    file=display_path, old=old_count, new=new_count,
                )))

        reasons = []
        if candidates:
            reasons = _filter_first_time_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check test weakening: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings=' '.join(reasons))
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
