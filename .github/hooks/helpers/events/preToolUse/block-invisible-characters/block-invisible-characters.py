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
import unicodedata
import uuid
from contextlib import closing
from typing import cast


_EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
_CHARACTER_PATTERNS_KEY = 'characterPatterns'
# 既存の行と照合するときに行の前後から除く文字。`str.strip()` は全角スペースやノーブレークスペースも除くため、
# 行頭・行末の全角スペースだけを加えた編集を既存の行とみなさないよう、ASCIIの空白だけを除く。
_LINE_TRIM_CHARACTERS = ' \t\r'
_MAX_SNIPPETS = 3
_MAX_SNIPPET_LENGTH = 80
_FINDING = '{file} would get {kinds} ({snippets})'
_DENY_REASON = (
    'Edit denied: {findings}. These characters are invisible or look like ordinary spaces, so they slip past review '
    'and can break parsing, string comparison, indentation, or change how code reads (bidirectional controls can '
    'reorder what is displayed). Remove them, or replace them with ordinary spaces. If one is intended, for example '
    'test data or a deliberate string, write it by its code point so it is visible, such as chr(0x3000) in Python or '
    'String.fromCodePoint(0x3000) in JavaScript. If you write it as an escape sequence instead, read the file after '
    'the edit and confirm that the escape was not turned into the character itself. Retry this edit unchanged only if '
    'neither removing nor rewriting the character is appropriate; this check denies it only once per file.'
)


def _compile_patterns(patterns, key):
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {key}: {ex}') from None


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('invisible character definitions must be a JSON object.')
    excluded = definitions.get(_EXCLUDED_FILE_PATTERNS_KEY)
    if not isinstance(excluded, list) or not all(isinstance(item, str) and item.strip() for item in excluded):
        raise ValueError(
            f'invisible character definitions require an array of non-empty strings for {_EXCLUDED_FILE_PATTERNS_KEY}.'
        )
    character_definitions = definitions.get(_CHARACTER_PATTERNS_KEY)
    if not isinstance(character_definitions, list) or not character_definitions or not all(
        isinstance(definition, dict) and all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        )
        for definition in character_definitions
    ):
        raise ValueError(
            'invisible character definitions require a non-empty array of objects with non-empty strings for name '
            f'and pattern in {_CHARACTER_PATTERNS_KEY}.'
        )
    character_patterns = _compile_patterns(
        [definition['pattern'] for definition in character_definitions], _CHARACTER_PATTERNS_KEY,
    )
    return (
        _compile_patterns(excluded, _EXCLUDED_FILE_PATTERNS_KEY),
        [(definition['name'], pattern) for definition, pattern in zip(character_definitions, character_patterns)],
    )


def _read_existing(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()
    except OSError:
        return None


def _get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path


def _make_visible(text):
    # 理由で示す行の、見えない文字とASCII以外の空白を `<U+XXXX>` に置き換える。
    return ''.join(
        f'<U+{ord(character):04X}>'
        if unicodedata.category(character) == 'Cf' or (unicodedata.category(character) == 'Zs' and character != ' ')
        else character
        for character in text
    )


def _find_invisible_characters(character_patterns, written, existing):
    # 戻り値は (種類の名前のリスト, 該当行のリスト)。既存の内容に同じ行があるものは、元からあるものとみなす。
    existing_lines = {line.strip(_LINE_TRIM_CHARACTERS) for line in existing.splitlines()}
    kinds = []
    lines = []
    for line in written.splitlines():
        matched = [name for name, pattern in character_patterns if pattern.search(line)]
        trimmed = line.strip(_LINE_TRIM_CHARACTERS)
        if not matched or trimmed in existing_lines:
            continue
        if trimmed not in lines:
            lines.append(trimmed)
        kinds.extend(name for name in matched if name not in kinds)
    return kinds, lines


def _format_snippet(line):
    snippet = _make_visible(line)
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
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'invisible-character-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, character_patterns = _load_definitions(parsed_arguments.definitions_path)

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
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        for change in extract_edit_tool_changes(tool_name, hook_input['toolArgs']):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = _get_display_path(path, real_cwd)
            # パターンはOSによらず `/` 区切りのパスに対して照合する。
            if any(pattern.search(display_path.replace(os.sep, '/')) for pattern in excluded_file_patterns):
                continue
            current = (_read_existing(path) if os.path.isfile(path) else None) or ''
            kinds, lines = _find_invisible_characters(
                character_patterns, change['written'], '\n'.join([change['removed'], current]),
            )
            if lines:
                # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                file_key = hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()
                candidates.append((file_key, _FINDING.format(
                    file=display_path, kinds=', '.join(kinds),
                    snippets=', '.join(_format_snippet(line) for line in lines[:_MAX_SNIPPETS]),
                )))

        reasons = []
        if candidates:
            # hookは呼び出しごとに別プロセスなので、拒否済みのファイルをセッションUUID別に保存する。
            # DBファイル自体は自動削除しない。
            state_path = os.path.join(
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-invisible-characters',
                f'{parsed_session_id}.sqlite3',
            )
            reasons = _record_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check invisible characters: {ex}', file=sys.stderr)
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
