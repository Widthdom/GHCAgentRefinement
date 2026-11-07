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
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
# 削除は再生成で失われる手編集ではないため、追加と更新だけを対象とする。
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:Add|Update) File: (.+?)\s*$', re.MULTILINE)
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
        if not isinstance(patterns, list) or not patterns or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in patterns
        ):
            raise ValueError(f'generated file definitions require a non-empty array of non-empty strings for {key}.')
        try:
            compiled.append([re.compile(pattern) for pattern in patterns])
        except re.PatternError as ex:
            raise ValueError(f'invalid regular expression in {key}: {ex}') from None
    return compiled[0], compiled[1]


def _get_paths(tool_name, tool_args):
    if tool_name == 'apply_patch':
        return [match.group(1) for text in _collect_json_string_values(tool_args) for match in _PATCH_FILE_PATTERN.finditer(text)]
    if not isinstance(tool_args, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    return [path] if isinstance(path, str) and path.strip() else []


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
        candidates = {}
        tool_name = tool_name.removeprefix('functions.')
        if tool_name in _EDIT_TOOL_NAMES:
            for relative_path in _get_paths(tool_name, _unwrap_tool_args(hook_input['toolArgs'])):
                path = os.path.realpath(os.path.join(cwd, relative_path))
                # パスの判定は、Windowsのnormcaseで小文字にする前のパスで行い、大文字小文字を区別できるようにする。
                evidence = _get_evidence(path_patterns, marker_patterns, path, _get_display_path(path, real_cwd))
                if evidence:
                    # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                    candidates.setdefault(hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest(), evidence)

        evidence = []
        if candidates:
            # hookは呼び出しごとに別プロセスなので、拒否済みのファイルをセッションUUID別に保存する。
            # DBファイル自体は自動削除しない。
            state_path = os.path.join(
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-generated-file-edits',
                f'{parsed_session_id}.sqlite3',
            )
            evidence = _filter_first_time_denials(state_path, candidates.items())
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check generated file edits: {ex}', file=sys.stderr)
        return 2

    if evidence:
        reason = _DENY_REASON.format(evidence=', '.join(evidence))
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
