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
_is_non_blank_string_list = runpy.run_path(os.path.join(
    _utilities_directory, 'check-string-list', 'check-string-list.py',
))['is_non_blank_string_list']
_get_path_sha256_digest = runpy.run_path(os.path.join(
    _utilities_directory, 'get-sha256-digest', 'get-sha256-digest.py',
))['get_path_sha256_digest']
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_get_edited_paths = runpy.run_path(os.path.join(_utilities_directory, 'edit-tool-changes', 'edit-tool-changes.py'))['get_edited_paths']
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']


# 削除は再生成で失われる手編集ではないため、追加と更新だけを対象とする。
_PATCH_OPERATIONS = frozenset({'Add File', 'Update File'})
_GENERATED_PATH_PATTERNS_KEY = 'generatedPathPatterns'
_GENERATED_MARKER_PATTERNS_KEY = 'generatedMarkerPatterns'
# 生成の目印は、ライセンスなどの見出しに続くファイル先頭の数行に置かれる。
_MARKER_HEAD_LINES = 10
_MAX_MARKER_LENGTH = 80
_PATH_EVIDENCE = '{file} (path matches a generated file pattern)'
_MARKER_EVIDENCE = '{file} (header contains "{marker}")'
_DENY_REASON = (
    'Edit denied: these files look generated: {evidence}. Hand edits to a generated file are lost when it is '
    'regenerated. Edit the source the file is generated from (for example a .resx, .edmx, .proto, schema, or '
    'package manifest) and regenerate it with its tool. If no source or generator exists, or the user asked to '
    'edit the file directly, retry the same edit; this check denies it only once.'
)


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('generated file definitions must be a JSON object.')
    compiled = []
    for key in (_GENERATED_PATH_PATTERNS_KEY, _GENERATED_MARKER_PATTERNS_KEY):
        patterns = definitions.get(key)
        if not _is_non_blank_string_list(patterns):
            raise ValueError(f'generated file definitions require a non-empty array of non-blank strings for {key}.')
        compiled.append(_build_regex_matchers(patterns, key))
    return compiled[0], compiled[1]


def _read_head(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return [line for _, line in zip(range(_MARKER_HEAD_LINES), file)]
    except OSError:
        return []


def _get_evidence(path_patterns, marker_patterns, path, display_path):
    # パターンは、get-display-pathがOSによらず `/` 区切りで返すパスに対して照合する。
    if any(pattern.search(display_path) for pattern in path_patterns):
        return _PATH_EVIDENCE.format(file=display_path)
    # 新規ファイルには再生成で失われる手編集がないため、目印は既存のファイルだけで確かめる。
    if os.path.isfile(path):
        for line in _read_head(path):
            if any(pattern.search(line) for pattern in marker_patterns):
                return _MARKER_EVIDENCE.format(file=display_path, marker=line.strip()[:_MAX_MARKER_LENGTH])
    return None


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'generated-file-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        path_patterns, marker_patterns = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-generated-file-edits', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        real_cwd = os.path.realpath(cwd)
        candidates = {}
        for relative_path in _get_edited_paths(tool_name, tool_args, _PATCH_OPERATIONS):
            path = os.path.realpath(os.path.join(cwd, relative_path))
            # パスの判定は、Windowsのnormcaseで小文字にする前のパスで行い、大文字小文字を区別できるようにする。
            evidence = _get_evidence(path_patterns, marker_patterns, path, _get_display_path(path, real_cwd))
            if evidence:
                # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                candidates.setdefault(_get_path_sha256_digest(path), evidence)

        evidence = []
        if candidates:
            evidence = _filter_first_time_denials(state_path, candidates.items())
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check generated file edits: {ex}', file=sys.stderr)
        return 2

    if evidence:
        reason = _DENY_REASON.format(evidence=', '.join(evidence))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
