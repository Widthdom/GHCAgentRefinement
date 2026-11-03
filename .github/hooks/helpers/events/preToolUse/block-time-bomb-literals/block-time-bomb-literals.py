import argparse
import datetime
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
from contextlib import closing
from typing import cast


TEST_FILE_PATTERNS_KEY = 'testFilePatterns'
# 文字列の中の日付（`2026-11-03`・`2026/11/03`）。バージョン番号（`1.2026-...`）の一部は除く。
ISO_DATE_PATTERN = re.compile(r'(?<![\d.])(20\d{2})([-/])(\d{1,2})\2(\d{1,2})(?!\d)')
# 年・月・日を引数に取る日付の生成（C#・Python・Java・Kotlin・Ruby・Go・Rust）。月は1始まり。
# Goの月は `time.November` のような名前でもよい。
CONSTRUCTOR_DATE_PATTERN = re.compile(
    r'\b(?:DateTime(?:Offset)?|DateOnly|DateTimeImmutable|datetime|date|Date\.new|DateTime\.new'
    r'|Time\.(?:new|local|utc|gm|mktime)|(?:LocalDate|LocalDateTime|ZonedDateTime|OffsetDateTime)\.of'
    r'|time\.Date|NaiveDate::from_ymd(?:_opt)?)\s*\(\s*(20\d{2})\s*,\s*(?:time\.)?(\d{1,2}|[A-Z][a-z]+)\s*,\s*(\d{1,2})(?!\d)'
)
# JavaScriptの `new Date(年, 月, 日)`・`Date.UTC(年, 月, 日)`。月は0始まり。
ZERO_BASED_MONTH_DATE_PATTERN = re.compile(
    r'(?:\bnew\s+Date|\bDate\.UTC)\s*\(\s*(20\d{2})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})(?!\d)'
)
MONTH_NAMES = {
    name: number for number, name in enumerate(
        ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
         'November', 'December'), 1,
    )
}
# コメント行の日付は判定に使わない。
COMMENT_LINE_PATTERN = re.compile(r'^\s*(?:#|//|/\*|\*|--|<!--)')
FINDING = '{file} writes {literal} ({date}) and reads the current time with {clock}'
DENY_REASON = (
    'Edit denied: {findings}. Today is {today}, so the test compares a fixed date close to today with the real '
    'clock and will start failing (or silently change meaning) once that date passes. '
    'Derive the date from the current time (for example today + 30 days), or freeze the clock with a fake '
    '(TimeProvider/FakeTimeProvider, freezegun, jest.useFakeTimers().setSystemTime, Clock.fixed, etc.) so the test '
    'does not depend on the day it runs. If the date is intended to be absolute, retry the same edit; this check '
    'denies it only once per file.'
)


def load_test_file_patterns(path):
    # テストファイルの判定はblock-test-weakening hookの定義を共有し、二重管理しない。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    patterns = definitions.get(TEST_FILE_PATTERNS_KEY) if isinstance(definitions, dict) else None
    if not isinstance(patterns, list) or not patterns or not all(
        isinstance(pattern, str) and pattern.strip() for pattern in patterns
    ):
        raise ValueError(f'test file definitions require a non-empty array of non-empty strings for {TEST_FILE_PATTERNS_KEY}.')
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {TEST_FILE_PATTERNS_KEY}: {ex}') from None


def load_definitions(path):
    # 戻り値は {pastDays, futureDays, clockPatterns, frozenClockPatterns}。正規表現はコンパイル済み。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('time bomb definitions must be a JSON object.')
    loaded = {}
    for key in ('pastDays', 'futureDays'):
        value = definitions.get(key)
        if type(value) is not int or value < 0:
            raise ValueError(f'time bomb definitions require a non-negative integer for {key}.')
        loaded[key] = value
    for key in ('clockPatterns', 'frozenClockPatterns'):
        patterns = definitions.get(key)
        if not isinstance(patterns, list) or not patterns or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in patterns
        ):
            raise ValueError(f'time bomb definitions require a non-empty array of non-empty strings for {key}.')
        try:
            loaded[key] = [re.compile(pattern) for pattern in patterns]
        except re.PatternError as ex:
            raise ValueError(f'invalid regular expression in {key}: {ex}') from None
    return loaded


def read_existing(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()
    except OSError:
        return None


def get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示す。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path.replace(os.sep, '/')


def to_date(year, month, day):
    # 戻り値は日付。存在しない日付（2月30日など）や読めない月名はNone。
    month_number = int(month) if month.isdigit() else MONTH_NAMES.get(month)
    if month_number is None:
        return None
    try:
        return datetime.date(int(year), month_number, int(day))
    except ValueError:
        return None


def find_date_literals(text):
    # 戻り値は (書かれた文字列, 日付) のリスト。コメント行は除く。
    found = []
    for line in text.splitlines():
        if COMMENT_LINE_PATTERN.match(line):
            continue
        for match in ISO_DATE_PATTERN.finditer(line):
            found.append((match.group(0), to_date(match.group(1), match.group(3), match.group(4))))
        for match in CONSTRUCTOR_DATE_PATTERN.finditer(line):
            found.append((match.group(0), to_date(match.group(1), match.group(2), match.group(3))))
        for match in ZERO_BASED_MONTH_DATE_PATTERN.finditer(line):
            found.append((match.group(0), to_date(match.group(1), str(int(match.group(2)) + 1), match.group(3))))
    return [(literal, date) for literal, date in found if date is not None]


def get_added_text(removed, written):
    # 置換前にもあった行は、今回書き加えた日付ではないため除く。
    removed_lines = {line.strip() for line in removed.splitlines()}
    return '\n'.join(line for line in written.splitlines() if line.strip() not in removed_lines)


def find_time_bomb(definitions, today, added, content):
    # 戻り値は最初に見つけた (日付の文字列, 日付, 現在時刻の参照)。見つからなければNone。
    if any(pattern.search(content) for pattern in definitions['frozenClockPatterns']):
        return None
    clock = next((match.group(0) for pattern in definitions['clockPatterns'] if (match := pattern.search(content))), None)
    if clock is None:
        return None
    earliest = today - datetime.timedelta(days=definitions['pastDays'])
    latest = today + datetime.timedelta(days=definitions['futureDays'])
    for literal, date in find_date_literals(added):
        if earliest <= date <= latest:
            return literal, date, clock
    return None


def record_denial(state_path, path):
    # 戻り値は、このファイルを今回初めて拒否するか。同じファイルの拒否はセッションで1回だけにする。
    # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    file_key = hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS denials (file TEXT PRIMARY KEY)')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            return bool(connection.execute('INSERT OR IGNORE INTO denials (file) VALUES (?)', (file_key,)).rowcount)


def parse_today(value):
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'invalid date: {value}') from None


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルと今日の日付を差し替えられるようにする。
    script_directory = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument('--definitions-path', default=os.path.join(script_directory, 'time-bomb-definitions.json'))
    parser.add_argument(
        '--test-file-definitions-path',
        default=os.path.join(
            os.path.dirname(script_directory), 'block-test-weakening', 'test-weakening-definitions.json',
        ),
    )
    parser.add_argument('--today', type=parse_today, default=None)
    parsed_arguments = parser.parse_args(arguments)
    today = parsed_arguments.today or datetime.date.today()

    try:
        definitions = load_definitions(parsed_arguments.definitions_path)
        test_file_patterns = load_test_file_patterns(parsed_arguments.test_file_definitions_path)

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

        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        changes = extract_edit_tool_changes(tool_name, hook_input['toolArgs'])

        real_cwd = os.path.realpath(cwd)
        candidates = []
        for change in changes:
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = get_display_path(path, real_cwd)
            # テストファイルのパターンはOSによらず `/` 区切りのパスに対して照合する。
            if not any(pattern.search(display_path) for pattern in test_file_patterns):
                continue
            current = (read_existing(path) if os.path.isfile(path) else None) or ''
            # 上書きではファイル全体と現在の内容、それ以外では書き込む内容と置換前の内容を比べる。
            # 現在時刻の参照は、編集後のファイルにあればよいため、現在の内容と書き込む内容の両方から探す。
            if change['full_content'] is not None:
                added = get_added_text(current, change['full_content'])
                content = change['full_content']
            else:
                added = get_added_text(change['removed'], change['written'])
                content = current + '\n' + change['written']
            time_bomb = find_time_bomb(definitions, today, added, content)
            if time_bomb:
                candidates.append((path, display_path, time_bomb))

        findings = []
        if candidates:
            # hookは呼び出しごとに別プロセスなので、拒否済みのファイルをセッションUUID別に保存する。
            # DBファイル自体は自動削除しない。
            state_path = os.path.join(
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-time-bomb-literals',
                f'{parsed_session_id}.sqlite3',
            )
            for path, display_path, (literal, date, clock) in candidates:
                if record_denial(state_path, path):
                    findings.append(FINDING.format(file=display_path, literal=literal, date=date.isoformat(), clock=clock))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check time bomb literals: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = DENY_REASON.format(findings='; '.join(findings), today=today.isoformat())
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
