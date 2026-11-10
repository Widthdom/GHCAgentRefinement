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
    # 定義はUTF-8のJSON配列として読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, list) or not definitions:
        raise ValueError('elision definitions must be a non-empty JSON array.')
    compiled = []
    for definition in definitions:
        if not isinstance(definition, dict) or not all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        ):
            raise ValueError('each elision definition requires non-empty strings for name and pattern.')
        compiled.extend(_build_regex_matchers([definition['pattern']], f"elision definition '{definition['name']}'"))
    return compiled


def _find_elision_lines(definitions, written, existing):
    # 既存の内容に同じ行があるプレースホルダーは、元からあるものとみなす。
    existing_lines = {line.strip() for line in existing.splitlines()}
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
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
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

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-elided-rewrites', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        normalized_cwd = os.path.normcase(os.path.realpath(cwd))
        # (拒否のキー, 理由) のリスト。同じファイルでも種類の異なる拒否は、それぞれ1回ずつ行う。
        candidates = []
        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        for change in extract_edit_tool_changes(tool_name, tool_args):
            path = os.path.normcase(os.path.realpath(os.path.join(cwd, change['path'])))
            display_path = _get_display_path(path, normalized_cwd)
            # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
            file_key = hashlib.sha256(path.encode('utf-8')).hexdigest()
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
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check elided rewrites: {ex}', file=sys.stderr)
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
