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
from collections import Counter
from contextlib import closing
from typing import cast


_EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
_TIMING_PATTERNS_KEY = 'timingPatterns'
# 数値リテラル。`100ms`・`5000L`・`0.5f` のような単位や型の接尾辞は数値に含めず、行の形の側に残す。
# 単位が変わる書き換え（`5m` から `300s`）は行の形が一致しないため、大小を比べない。
_NUMBER_PATTERN = re.compile(r'(?<![\w.])\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?(?=[A-Za-z]*\b)')
_NUMBER_PLACEHOLDER = '\0'
# 空行とコメントだけの行は、ロジックの変更とみなさない。
_NEUTRAL_LINE_PATTERN = re.compile(r'^(?:$|//|#|/\*|\*|--|<!--)')
_MAX_SNIPPETS = 3
_MAX_SNIPPET_LENGTH = 80
_FINDING = '{file} would only raise timing values ({snippets})'
_DENY_REASON = (
    'Edit denied: {findings}. This edit raises timeouts, delays, sleeps, or retry counts without changing any logic. '
    'Waiting longer hides why an operation is slow or flaky instead of fixing it. Find the cause, such as a race, a '
    'missing await, a wrong wait condition, or real slowness, and fix that. If the larger value is genuinely needed, '
    'for example when the user asked for it or the operation is known to take that long, retry the same edit; this '
    'check denies it only once per file.'
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
        raise ValueError('timeout inflation definitions must be a JSON object.')
    loaded = []
    for key, allow_empty in ((_EXCLUDED_FILE_PATTERNS_KEY, True), (_TIMING_PATTERNS_KEY, False)):
        patterns = definitions.get(key)
        if not isinstance(patterns, list) or (not allow_empty and not patterns) or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in patterns
        ):
            raise ValueError(f'timeout inflation definitions require an array of non-empty strings for {key}.')
        loaded.append(_compile_patterns(patterns, key))
    return loaded[0], loaded[1]


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


def _get_changed_lines(text, other):
    # textにあってotherにない行を、重複を含めて返す。前後の空白と、空行・コメントだけの行は除く。
    changed = Counter(line.strip() for line in text.splitlines()) - Counter(line.strip() for line in other.splitlines())
    return [line for line in changed.elements() if not _NEUTRAL_LINE_PATTERN.match(line)]


def _split_numbers(line):
    return _NUMBER_PATTERN.sub(_NUMBER_PLACEHOLDER, line), [
        float(number.replace('_', '')) for number in _NUMBER_PATTERN.findall(line)
    ]


def _is_inflated(old_numbers, new_numbers):
    # 同じ位置の数値が1つ以上増え、減ったものがない。
    return any(new > old for old, new in zip(old_numbers, new_numbers)) and all(
        new >= old for old, new in zip(old_numbers, new_numbers)
    )


def _find_inflation(timing_patterns, removed, written):
    # 書き換えた行がすべて、待ち時間・再試行の数値を増やすだけの書き換えなら、(書き換え前, 書き換え後) の行の組を返す。
    # ほかの変更（ロジックの変更、行の追加・削除）を1つでも含む場合は空のリストを返す。
    old_lines = _get_changed_lines(removed, written)
    new_lines = _get_changed_lines(written, removed)
    if not new_lines or len(old_lines) != len(new_lines):
        return []
    pairs = []
    for new_line in new_lines:
        if not any(pattern.search(new_line) for pattern in timing_patterns):
            return []
        new_skeleton, new_numbers = _split_numbers(new_line)
        match = next((
            old_line for old_line in old_lines
            if _split_numbers(old_line)[0] == new_skeleton and _is_inflated(_split_numbers(old_line)[1], new_numbers)
        ), None)
        if match is None:
            return []
        old_lines.remove(match)
        pairs.append((match, new_line))
    return pairs


def _shorten(line):
    return line if len(line) <= _MAX_SNIPPET_LENGTH else line[:_MAX_SNIPPET_LENGTH] + '...'


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
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'timeout-inflation-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, timing_patterns = _load_definitions(parsed_arguments.definitions_path)

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
            # 上書きではファイル全体、それ以外では置換前の内容と比べる。
            current = (_read_existing(path) if os.path.isfile(path) else None) or ''
            if change['full_content'] is not None and current:
                removed, written = current, change['full_content']
            else:
                removed, written = change['removed'], change['written']
            pairs = _find_inflation(timing_patterns, removed, written)
            if pairs:
                # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                file_key = hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()
                candidates.append((file_key, _FINDING.format(file=display_path, snippets=', '.join(
                    f'"{_shorten(old)}" -> "{_shorten(new)}"' for old, new in pairs[:_MAX_SNIPPETS]
                ))))

        reasons = []
        if candidates:
            # hookは呼び出しごとに別プロセスなので、拒否済みのファイルをセッションUUID別に保存する。
            # DBファイル自体は自動削除しない。
            state_path = os.path.join(
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-timeout-inflation',
                f'{parsed_session_id}.sqlite3',
            )
            reasons = _record_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check timeout inflation: {ex}', file=sys.stderr)
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
