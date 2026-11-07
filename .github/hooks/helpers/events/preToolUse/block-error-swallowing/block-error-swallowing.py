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
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
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
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('error swallowing definitions must be a JSON object.')
    excluded = definitions.get(_EXCLUDED_FILE_PATTERNS_KEY)
    if not isinstance(excluded, list) or not all(isinstance(item, str) and item.strip() for item in excluded):
        raise ValueError(
            f'error swallowing definitions require an array of non-empty strings for {_EXCLUDED_FILE_PATTERNS_KEY}.'
        )
    swallowing_definitions = definitions.get(_SWALLOWING_PATTERNS_KEY)
    if not isinstance(swallowing_definitions, list) or not swallowing_definitions or not all(
        isinstance(definition, dict) and all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        )
        for definition in swallowing_definitions
    ):
        raise ValueError(
            f'error swallowing definitions require a non-empty array of objects with non-empty strings for name and '
            f'pattern in {_SWALLOWING_PATTERNS_KEY}.'
        )
    # 行全体に一致させる定義のため、行頭・行末は各行に一致させる。
    swallowing_patterns = _build_regex_matchers(
        [definition['pattern'] for definition in swallowing_definitions], _SWALLOWING_PATTERNS_KEY, re.MULTILINE,
    )
    return (
        _build_regex_matchers(excluded, _EXCLUDED_FILE_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(swallowing_definitions, swallowing_patterns)],
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
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'error-swallowing-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, swallowing_patterns = _load_definitions(parsed_arguments.definitions_path)

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
        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        for change in extract_edit_tool_changes(tool_name, hook_input['toolArgs']):
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
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-error-swallowing',
                f'{parsed_session_id}.sqlite3',
            )
            reasons = _filter_first_time_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check error swallowing: {ex}', file=sys.stderr)
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
