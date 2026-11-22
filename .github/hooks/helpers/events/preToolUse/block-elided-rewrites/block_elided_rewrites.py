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
    os.path.join(_utilities_directory, 'get-sha256-digest'),
    os.path.join(_utilities_directory, 'read-definitions'),
    os.path.join(_utilities_directory, 'get-state-path'),
    os.path.join(_utilities_directory, 'filter-first-time-denials'),
    os.path.join(_utilities_directory, 'get-display-path'),
    os.path.join(_utilities_directory, 'read-stored-file-content-for-write-comparison'),
    os.path.join(_utilities_directory, 'get-stripped-lines'),
]
import read_hook_input as _hook_input
import write_hook_output as _hook_output
from get_sha256_digest import get_path_sha256_digest as _get_path_sha256_digest
import read_definitions as _definitions
from get_state_path import get_state_path as _get_state_path
from filter_first_time_denials import filter_first_time_denials as _filter_first_time_denials
from get_display_path import get_display_path as _get_display_path
from read_stored_file_content_for_write_comparison import read_stored_file_content_for_write_comparison as _read_stored_file_content_for_write_comparison
from get_stripped_lines import get_stripped_lines as _get_stripped_lines


# 上書きで、既存の行数がこれ以上あり、新しい行数がこの割合未満になれば縮小とみなす。
_MIN_SHRINK_LINES = 20
_SHRINK_RATIO = 0.5
_ELISION = 'elision'
_SHRINK = 'shrink'
_ELISION_REASON = (
    '{files} would get placeholder comments that stand in for omitted content ({lines}). '
    'Writing such a placeholder replaces the real content with the comment and silently deletes it.'
)
_SHRINK_REASON = (
    '{file} would be overwritten with {new} lines although it currently has {old} lines.'
)
_DENY_REASON = (
    'Edit denied: {findings} Write the complete content, or use a targeted edit that replaces only the part you '
    'change. If the placeholder or the removal is intended, for example when the user asked for it, '
    'retry the same edit; this check denies it only once.'
)


def _load_definitions(path):
    label = 'elision definitions'
    return [pattern for _, pattern in _definitions.build_named_regex_matchers(
        _definitions.read_definitions(path, label, list), label,
    )]


def _find_elision_lines(definitions, written, existing):
    # 既存の内容に同じ行があるプレースホルダーは、元からあるものとみなす。
    existing_lines = _get_stripped_lines(existing)
    lines = []
    for pattern in definitions:
        for match in pattern.finditer(written):
            line = match.group(0).strip()
            if line not in existing_lines and line not in lines:
                lines.append(line)
    return lines


def _count_lines(text):
    return len(text.splitlines())


def main(arguments=None):
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'elision-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input.read_hook_input()
        state_path = _get_state_path('block-elided-rewrites', hook_input.get('sessionId'))
        cwd = _hook_input.get_cwd(hook_input)
        tool_name, tool_args = _hook_input.get_tool_call(hook_input)

        real_cwd = os.path.realpath(cwd)
        # (拒否のキー, 理由) のリスト。同じファイルでも種類の異なる拒否は、それぞれ1回ずつ行う。
        candidates = []
        # 編集ツールの引数の解析は、共通utilityで行う。
        sys.path.insert(0, os.path.join(_utilities_directory, 'edit-tool-changes'))
        from edit_tool_changes import extract_edit_tool_changes
        for change in extract_edit_tool_changes(tool_name, tool_args):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = _get_display_path(path, real_cwd)
            # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。normcaseで、同じファイルの別表記を同一と判定する。
            file_key = _get_path_sha256_digest(path)
            current = _read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None
            # 新規ファイルには消える内容がないため、骨組みとしてのプレースホルダーを許可する。
            if current is None:
                continue

            lines = _find_elision_lines(definitions, change['written'], '\n'.join([change['removed'], current]))
            if lines:
                candidates.append((f'{file_key}:{_ELISION}', _ELISION_REASON.format(
                    files=display_path, lines=', '.join(f'"{line}"' for line in lines[:3]),
                )))

            if change['full_content'] is not None:
                old_lines = _count_lines(current)
                new_lines = _count_lines(change['full_content'])
                if old_lines >= _MIN_SHRINK_LINES and new_lines < old_lines * _SHRINK_RATIO:
                    candidates.append((f'{file_key}:{_SHRINK}', _SHRINK_REASON.format(
                        file=display_path, new=new_lines, old=old_lines,
                    )))

        reasons = []
        if candidates:
            reasons = _filter_first_time_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, ImportError, sqlite3.Error) as ex:
        print(f'Failed to check elided rewrites: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings=' '.join(reasons))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output.write_deny_decision(reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
