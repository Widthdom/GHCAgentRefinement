import argparse
import hashlib
import io
import json
import os
import re
import runpy
import sqlite3
import sys
import tempfile
import uuid
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
# 置換前の内容を表す引数。書き込む内容ではないため検査せず、既存の内容として照合に使う。
_REMOVED_TEXT_KEYS = {'old_str', 'old_string'}
# 書き込む内容ではない引数。
_IGNORED_KEYS = {'path', 'command'}
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+?)\s*$')
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


def _get_patch_writes(text):
    # パッチをファイルごとに分け、追加行（`+`）を書き込む内容、削除行（`-`）を置換前の内容とする。
    # 削除するファイルには何も書き込まないため対象外とする。
    writes = []
    for line in text.splitlines():
        match = _PATCH_FILE_PATTERN.match(line)
        if match:
            writes.append({'path': match.group(2), 'written': [], 'removed': [], 'deleted': match.group(1) == 'Delete'})
        elif writes and line.startswith('+'):
            writes[-1]['written'].append(line[1:])
        elif writes and line.startswith('-'):
            writes[-1]['removed'].append(line[1:])
    return [
        (write['path'], '\n'.join(write['written']), '\n'.join(write['removed']))
        for write in writes if not write['deleted']
    ]


def _get_writes(tool_name, tool_args):
    # 戻り値は (パス, 書き込む内容, 置換前の内容) のリスト。
    if tool_name == 'apply_patch':
        return [write for text in _collect_json_string_values(tool_args) for write in _get_patch_writes(text)]
    if not isinstance(tool_args, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    if not isinstance(path, str) or not path.strip():
        return []
    return [(
        path,
        '\n'.join(_collect_json_string_values(tool_args, _IGNORED_KEYS | _REMOVED_TEXT_KEYS)),
        '\n'.join(text for key in _REMOVED_TEXT_KEYS for text in _collect_json_string_values(tool_args.get(key))),
    )]


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
    if len(snippet) > _MAX_SNIPPET_LENGTH:
        snippet = snippet[:_MAX_SNIPPET_LENGTH] + '...'
    return f'"{snippet}"'


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

        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        session_id = hook_input.get('sessionId')
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('sessionId must be a non-empty UUID string.')
        try:
            parsed_session_id = uuid.UUID(session_id.strip())
        except ValueError:
            raise ValueError('sessionId must be a valid UUID.') from None
        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd must be a non-empty string.')
        if not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        # preToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        real_cwd = os.path.realpath(cwd)
        candidates = []
        tool_name = tool_name.removeprefix('functions.')
        if tool_name in _EDIT_TOOL_NAMES:
            for write_path, written, removed in _get_writes(tool_name, _unwrap_tool_args(hook_input['toolArgs'])):
                path = os.path.realpath(os.path.join(cwd, write_path))
                display_path = _get_display_path(path, real_cwd)
                if any(pattern.search(display_path) for pattern in excluded_file_patterns):
                    continue
                current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
                kinds, snippets = _find_native_paths(
                    native_patterns, normalization_patterns, written, '\n'.join([removed, current]),
                )
                if snippets:
                    # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                    file_key = hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()
                    candidates.append((file_key, _FINDING.format(
                        file=display_path, kinds=', '.join(kinds),
                        snippets=', '.join(_format_snippet(snippet) for snippet in snippets[:_MAX_SNIPPETS]),
                    )))

        reasons = []
        if candidates:
            # hookは呼び出しごとに別プロセスなので、拒否済みのファイルをセッションUUID別に保存する。
            # DBファイル自体は自動削除しない。
            state_path = os.path.join(
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-native-path-separators',
                f'{parsed_session_id}.sqlite3',
            )
            reasons = _filter_first_time_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check native path separators: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings='; '.join(reasons))
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
