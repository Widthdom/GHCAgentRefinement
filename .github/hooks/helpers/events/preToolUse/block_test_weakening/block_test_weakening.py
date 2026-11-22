import argparse
import os
import sqlite3
import sys


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'write_hook_output'),
    os.path.join(_utilities_directory, 'get_sha256_digest'),
    os.path.join(_utilities_directory, 'read_definitions'),
    os.path.join(_utilities_directory, 'check_test_files'),
    os.path.join(_utilities_directory, 'get_state_path'),
    os.path.join(_utilities_directory, 'filter_first_time_denials'),
    os.path.join(_utilities_directory, 'get_display_path'),
    os.path.join(_utilities_directory, 'read_stored_file_content_for_write_comparison'),
    os.path.join(_utilities_directory, 'get_stripped_lines'),
]
import read_hook_input as _hook_input
import write_hook_output as _hook_output
from get_sha256_digest import get_path_sha256_digest as _get_path_sha256_digest
import read_definitions as _definitions
import check_test_files as _check_test_files
from get_state_path import get_state_path as _get_state_path
from filter_first_time_denials import filter_first_time_denials as _filter_first_time_denials
from get_display_path import get_display_path as _get_display_path
from read_stored_file_content_for_write_comparison import read_stored_file_content_for_write_comparison as _read_stored_file_content_for_write_comparison
from get_stripped_lines import get_stripped_lines as _get_stripped_lines


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


def _load_definitions(path):
    label = 'test weakening definitions'
    definitions = _definitions.read_definitions(path, label)
    return (
        _definitions.get_regex_matchers(definitions, _ASSERTION_PATTERNS_KEY, label),
        _definitions.get_named_regex_matchers(definitions, _WEAKENING_PATTERNS_KEY, label),
    )


def _find_weakening_lines(weakening_patterns, written, existing):
    # 既存の内容に同じ行があるものは、元からあるものとみなす。
    existing_lines = _get_stripped_lines(existing)
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
    _hook_output.configure_output_streams()

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_weakening_definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        assertion_patterns, weakening_patterns = _load_definitions(parsed_arguments.definitions_path)
        test_file_patterns = _check_test_files.load_test_file_patterns()

        hook_input = _hook_input.read_hook_input()
        state_path = _get_state_path('block_test_weakening', hook_input.get('sessionId'))
        cwd = _hook_input.get_cwd(hook_input)
        tool_name, tool_args = _hook_input.get_tool_call(hook_input)

        real_cwd = os.path.realpath(cwd)
        # (拒否のキー, 理由) のリスト。同じファイルでも種類の異なる拒否は、それぞれ1回ずつ行う。
        candidates = []
        # 編集ツールの引数の解析は、共通utilityで行う。
        sys.path.insert(0, os.path.join(_utilities_directory, 'edit_tool_changes'))
        from edit_tool_changes import extract_edit_tool_changes
        for change in extract_edit_tool_changes(tool_name, tool_args, include_deleted=True):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            # テストファイルの判定は、Windowsのnormcaseで小文字にする前のパスで行い、大文字小文字を区別できるようにする。
            display_path = _get_display_path(path, real_cwd)
            if not _check_test_files.is_test_file(test_file_patterns, display_path):
                continue
            # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
            file_key = _get_path_sha256_digest(path)
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
    except (OSError, UnicodeError, ValueError, ImportError, sqlite3.Error) as ex:
        print(f'Failed to check test weakening: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings=' '.join(reasons))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output.write_deny_decision(reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
