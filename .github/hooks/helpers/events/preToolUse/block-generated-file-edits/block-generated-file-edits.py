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
EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
# 削除は再生成で失われる手編集ではないため、追加と更新だけを対象とする。
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:Add|Update) File: (.+?)\s*$', re.MULTILINE)
GENERATED_PATH_PATTERNS_KEY = 'generatedPathPatterns'
GENERATED_MARKER_PATTERNS_KEY = 'generatedMarkerPatterns'
# 生成の目印は、ライセンスなどの見出しに続くファイル先頭の数行に置かれる。
MARKER_HEAD_LINES = 10
MAX_MARKER_LENGTH = 80
PATH_EVIDENCE = '{file} (path matches a generated file pattern)'
MARKER_EVIDENCE = '{file} (header contains "{marker}")'
DENY_REASON = (
    'Edit denied: these files look generated: {evidence}. Hand edits to a generated file are lost when it is '
    'regenerated. Edit the source the file is generated from (for example a .resx, .edmx, .proto, schema, or '
    'package manifest) and regenerate it with its tool. If no source or generator exists, or the user asked to '
    'edit the file directly, retry the same edit; this check denies it only once.'
)


def load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('generated file definitions must be a JSON object.')
    compiled = []
    for key in (GENERATED_PATH_PATTERNS_KEY, GENERATED_MARKER_PATTERNS_KEY):
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


def get_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列（apply_patch）で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def collect_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item)


def get_paths(tool_name, tool_args):
    if tool_name == 'apply_patch':
        return [match.group(1) for text in collect_strings(tool_args) for match in PATCH_FILE_PATTERN.finditer(text)]
    if not isinstance(tool_args, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    return [path] if isinstance(path, str) and path.strip() else []


def read_head(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return [line for _, line in zip(range(MARKER_HEAD_LINES), file)]
    except OSError:
        return []


def get_display_path(path, cwd):
    # cwd配下のファイルは相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd)
    except ValueError:
        pass
    return path


def get_evidence(path_patterns, marker_patterns, path, display_path):
    # パターンはOSによらず `/` 区切りのパスに対して照合する。
    if any(pattern.search(display_path.replace(os.sep, '/')) for pattern in path_patterns):
        return PATH_EVIDENCE.format(file=display_path)
    # 新規ファイルには再生成で失われる手編集がないため、目印は既存のファイルだけで確かめる。
    if os.path.isfile(path):
        for line in read_head(path):
            if any(pattern.search(line) for pattern in marker_patterns):
                return MARKER_EVIDENCE.format(file=display_path, marker=line.strip()[:MAX_MARKER_LENGTH])
    return None


def record_denials(state_path, candidates):
    # candidates：(ファイルキー, 根拠) のリスト。戻り値は、今回初めて拒否する根拠のリスト。
    # 同じファイルの拒否は1回だけにし、意図した編集なら再実行で通す。
    # SQLiteのトランザクションで、並列のツール呼び出しでも重複して拒否しない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    evidence = []
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS denials (file TEXT NOT NULL PRIMARY KEY)')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for file_key, file_evidence in candidates:
                if connection.execute('INSERT OR IGNORE INTO denials (file) VALUES (?)', (file_key,)).rowcount:
                    evidence.append(file_evidence)
    return evidence


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'generated-file-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        path_patterns, marker_patterns = load_definitions(parsed_arguments.definitions_path)

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
        if tool_name in EDIT_TOOL_NAMES:
            for relative_path in get_paths(tool_name, get_tool_args(hook_input['toolArgs'])):
                path = os.path.realpath(os.path.join(cwd, relative_path))
                # パスの判定は、Windowsのnormcaseで小文字にする前のパスで行い、大文字小文字を区別できるようにする。
                evidence = get_evidence(path_patterns, marker_patterns, path, get_display_path(path, real_cwd))
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
            evidence = record_denials(state_path, list(candidates.items()))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check generated file edits: {ex}', file=sys.stderr)
        return 2

    if evidence:
        reason = DENY_REASON.format(evidence=', '.join(evidence))
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
