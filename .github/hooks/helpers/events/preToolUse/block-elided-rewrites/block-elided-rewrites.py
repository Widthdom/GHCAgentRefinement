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
# 置換前の内容を表す引数。書き込む内容ではないため検査せず、既存の内容として照合に使う。
REMOVED_TEXT_KEYS = {'old_str', 'old_string'}
# 書き込む内容ではない引数。
IGNORED_KEYS = {'path', 'command'}
# 内容を丸ごと書き込む引数。上書きでの縮小の判定に使う。
FULL_CONTENT_KEYS = ('file_text', 'content')
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:Add|Update) File: (.+?)\s*$', re.MULTILINE)
# 上書きで、既存の行数がこれ以上あり、新しい行数がこの割合未満になれば縮小とみなす。
MIN_SHRINK_LINES = 20
SHRINK_RATIO = 0.5
ELISION = 'elision'
SHRINK = 'shrink'
ELISION_REASON = (
    '{files} would get placeholder comments that stand in for omitted content ({lines}). '
    'Writing such a placeholder replaces the real content with the comment and silently deletes it.'
)
SHRINK_REASON = (
    '{file} would be overwritten with {new} lines although it currently has {old} lines.'
)
DENY_REASON = (
    'Edit denied: {findings} Write the complete content, or use a targeted edit that replaces only the part you '
    'change. If the placeholder or the removal is intended, for example when the user asked for it, '
    'retry the same edit; this check denies it only once.'
)


def load_definitions(path):
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
        try:
            compiled.append(re.compile(definition['pattern']))
        except re.PatternError as ex:
            raise ValueError(f"invalid regular expression in elision definition '{definition['name']}': {ex}") from None
    return compiled


def get_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列（apply_patch）で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def collect_strings(value, ignored_keys=frozenset()):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in ignored_keys:
                yield from collect_strings(item, ignored_keys)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item, ignored_keys)


def get_patch_writes(text):
    # パッチをファイルごとに分け、追加行（`+`）を書き込む内容、削除行（`-`）を既存の内容とする。
    writes = []
    for line in text.splitlines():
        match = PATCH_FILE_PATTERN.match(line)
        if match:
            writes.append({'path': match.group(1), 'written': [], 'removed': []})
        elif writes and line.startswith('+'):
            writes[-1]['written'].append(line[1:])
        elif writes and line.startswith('-'):
            writes[-1]['removed'].append(line[1:])
    return [
        {'path': write['path'], 'written': '\n'.join(write['written']), 'removed': '\n'.join(write['removed']),
         'full_content': None}
        for write in writes
    ]


def get_writes(tool_name, tool_args):
    # 戻り値は {path, written（書き込む内容）, removed（置換前の内容）, full_content（丸ごと書き込む内容）} のリスト。
    if tool_name == 'apply_patch':
        return [write for text in collect_strings(tool_args) for write in get_patch_writes(text)]
    if not isinstance(tool_args, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    if not isinstance(path, str) or not path.strip():
        return []
    full_content = None
    if tool_name == 'create' or (tool_name == 'str_replace_editor' and tool_args.get('command') == 'create'):
        full_content = next((tool_args[key] for key in FULL_CONTENT_KEYS if isinstance(tool_args.get(key), str)), None)
    return [{
        'path': path,
        'written': '\n'.join(collect_strings(tool_args, IGNORED_KEYS | REMOVED_TEXT_KEYS)),
        'removed': '\n'.join(text for key in REMOVED_TEXT_KEYS for text in collect_strings(tool_args.get(key))),
        'full_content': full_content,
    }]


def read_existing(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()
    except OSError:
        return None


def find_elision_lines(definitions, written, existing):
    # 既存の内容に同じ行があるプレースホルダーは、元からあるものとみなす。
    existing_lines = {line.strip() for line in existing.splitlines()}
    lines = []
    for pattern in definitions:
        for match in pattern.finditer(written):
            line = match.group(0).strip()
            if line not in existing_lines and line not in lines:
                lines.append(line)
    return lines


def count_lines(text):
    return len(text.splitlines())


def get_display_path(path, cwd):
    # cwd配下のファイルは相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd)
    except ValueError:
        pass
    return path


def record_denials(state_path, candidates):
    # candidates：(ファイルキー, 種類, 理由) のリスト。戻り値は、今回初めて拒否する理由のリスト。
    # 同じファイルの同じ種類の拒否は1回だけにし、意図した編集なら再実行で通す。
    # SQLiteのトランザクションで、並列のツール呼び出しでも重複して拒否しない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    reasons = []
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS denials (file TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (file, kind))'
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for file_key, kind, reason in candidates:
                if connection.execute(
                    'INSERT OR IGNORE INTO denials (file, kind) VALUES (?, ?)', (file_key, kind),
                ).rowcount:
                    reasons.append(reason)
    return reasons


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'elision-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = load_definitions(parsed_arguments.definitions_path)

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

        normalized_cwd = os.path.normcase(os.path.realpath(cwd))
        candidates = []
        tool_name = tool_name.removeprefix('functions.')
        if tool_name in EDIT_TOOL_NAMES:
            for write in get_writes(tool_name, get_tool_args(hook_input['toolArgs'])):
                path = os.path.normcase(os.path.realpath(os.path.join(cwd, write['path'])))
                display_path = get_display_path(path, normalized_cwd)
                # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                file_key = hashlib.sha256(path.encode('utf-8')).hexdigest()
                current = read_existing(path) if os.path.isfile(path) else None
                # 新規ファイルには消える内容がないため、骨組みとしてのプレースホルダーを許可する。
                if current is None:
                    continue

                lines = find_elision_lines(definitions, write['written'], '\n'.join([write['removed'], current]))
                if lines:
                    candidates.append((file_key, ELISION, ELISION_REASON.format(
                        files=display_path, lines=', '.join(f'"{line}"' for line in lines[:3]),
                    )))

                if write['full_content'] is not None:
                    old_lines = count_lines(current)
                    new_lines = count_lines(write['full_content'])
                    if old_lines >= MIN_SHRINK_LINES and new_lines < old_lines * SHRINK_RATIO:
                        candidates.append((file_key, SHRINK, SHRINK_REASON.format(
                            file=display_path, new=new_lines, old=old_lines,
                        )))

        reasons = []
        if candidates:
            # hookは呼び出しごとに別プロセスなので、拒否済みのファイルをセッションUUID別に保存する。
            # DBファイル自体は自動削除しない。
            state_path = os.path.join(
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-elided-rewrites',
                f'{parsed_session_id}.sqlite3',
            )
            reasons = record_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check elided rewrites: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = DENY_REASON.format(findings=' '.join(reasons))
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
