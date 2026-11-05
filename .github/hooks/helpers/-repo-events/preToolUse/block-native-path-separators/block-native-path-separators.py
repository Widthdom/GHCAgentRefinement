import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from contextlib import closing
from typing import cast


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


def _compile_patterns(patterns, key):
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {key}: {ex}') from None


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
    native_patterns = _compile_patterns(
        [definition['pattern'] for definition in native_definitions], _NATIVE_PATH_PATTERNS_KEY,
    )
    return (
        _compile_patterns(definitions[_EXCLUDED_FILE_PATTERNS_KEY], _EXCLUDED_FILE_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(native_definitions, native_patterns)],
        _compile_patterns(definitions[_NORMALIZATION_PATTERNS_KEY], _NORMALIZATION_PATTERNS_KEY),
    )


def _get_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列（apply_patch）で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def _collect_strings(value, ignored_keys=frozenset()):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in ignored_keys:
                yield from _collect_strings(item, ignored_keys)
    elif isinstance(value, list):
        for item in value:
            yield from _collect_strings(item, ignored_keys)


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
        return [write for text in _collect_strings(tool_args) for write in _get_patch_writes(text)]
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
        '\n'.join(_collect_strings(tool_args, _IGNORED_KEYS | _REMOVED_TEXT_KEYS)),
        '\n'.join(text for key in _REMOVED_TEXT_KEYS for text in _collect_strings(tool_args.get(key))),
    )]


def _read_existing(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()
    except OSError:
        return None


def _get_display_path(path, cwd):
    # cwd配下のファイルは、OSによらず `/` 区切りの相対パスで示す。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path


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


def _record_denials(state_path, candidates):
    # candidates：(ファイルキー, 理由) のリスト。戻り値は、今回初めて拒否する理由のリスト。
    # 同じファイルの拒否は1回だけにし、意図した編集なら再実行で通す。
    # SQLiteのトランザクションで、並列のツール呼び出しでも重複して拒否しない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    reasons = []
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS denials (file TEXT PRIMARY KEY)')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for file_key, reason in candidates:
                if connection.execute('INSERT OR IGNORE INTO denials (file) VALUES (?)', (file_key,)).rowcount:
                    reasons.append(reason)
    return reasons


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
            for write_path, written, removed in _get_writes(tool_name, _get_tool_args(hook_input['toolArgs'])):
                path = os.path.realpath(os.path.join(cwd, write_path))
                display_path = _get_display_path(path, real_cwd)
                if any(pattern.search(display_path) for pattern in excluded_file_patterns):
                    continue
                current = (_read_existing(path) if os.path.isfile(path) else None) or ''
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
            reasons = _record_denials(state_path, candidates)
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
