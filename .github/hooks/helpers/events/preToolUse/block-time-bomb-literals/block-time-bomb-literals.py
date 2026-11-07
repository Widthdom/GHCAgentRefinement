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
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_check_test_files = runpy.run_path(os.path.join(_utilities_directory, 'check-test-files', 'check-test-files.py'))
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']


# 文字列の中の日付（`2026-11-03`・`2026/11/03`）。バージョン番号（`1.2026-...`）の一部は除く。
_ISO_DATE_PATTERN = re.compile(r'(?<![\d.])(20\d{2})([-/])(\d{1,2})\2(\d{1,2})(?!\d)')
# 年・月・日を引数に取る日付の生成（C#・Python・Java・Kotlin・Ruby・Go・Rust）。月は1始まり。
# Goの月は `time.November` のような名前でもよい。
_CONSTRUCTOR_DATE_PATTERN = re.compile(
    r'\b(?:DateTime(?:Offset)?|DateOnly|DateTimeImmutable|datetime|date|Date\.new|DateTime\.new'
    r'|Time\.(?:new|local|utc|gm|mktime)|(?:LocalDate|LocalDateTime|ZonedDateTime|OffsetDateTime)\.of'
    r'|time\.Date|NaiveDate::from_ymd(?:_opt)?)\s*\(\s*(20\d{2})\s*,\s*(?:time\.)?(\d{1,2}|[A-Z][a-z]+)\s*,\s*(\d{1,2})(?!\d)'
)
# JavaScriptの `new Date(年, 月, 日)`・`Date.UTC(年, 月, 日)`。月は0始まり。
_ZERO_BASED_MONTH_DATE_PATTERN = re.compile(
    r'(?:\bnew\s+Date|\bDate\.UTC)\s*\(\s*(20\d{2})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})(?!\d)'
)
_MONTH_NAMES = {
    name: number for number, name in enumerate(
        ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
         'November', 'December'), 1,
    )
}
# コメント行の日付は判定に使わない。
_COMMENT_LINE_PATTERN = re.compile(r'^\s*(?:#|//|/\*|\*|--|<!--)')
_FINDING = '{file} writes {literal} ({date}) and reads the current time with {clock}'
_DENY_REASON = (
    'Edit denied: {findings}. Today is {today}, so the test compares a fixed date close to today with the real '
    'clock and will start failing (or silently change meaning) once that date passes. '
    'Derive the date from the current time (for example today + 30 days), or freeze the clock with a fake '
    '(TimeProvider/FakeTimeProvider, freezegun, jest.useFakeTimers().setSystemTime, Clock.fixed, etc.) so the test '
    'does not depend on the day it runs. If the date is intended to be absolute, retry the same edit; this check '
    'denies it only once per file.'
)


def _load_definitions(path):
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


def _to_date(year, month, day):
    # 戻り値は日付。存在しない日付（2月30日など）や読めない月名はNone。
    month_number = int(month) if month.isdigit() else _MONTH_NAMES.get(month)
    if month_number is None:
        return None
    try:
        return datetime.date(int(year), month_number, int(day))
    except ValueError:
        return None


def _find_date_literals(text):
    # 戻り値は (書かれた文字列, 日付) のリスト。コメント行は除く。
    found = []
    for line in text.splitlines():
        if _COMMENT_LINE_PATTERN.match(line):
            continue
        for match in _ISO_DATE_PATTERN.finditer(line):
            found.append((match.group(0), _to_date(match.group(1), match.group(3), match.group(4))))
        for match in _CONSTRUCTOR_DATE_PATTERN.finditer(line):
            found.append((match.group(0), _to_date(match.group(1), match.group(2), match.group(3))))
        for match in _ZERO_BASED_MONTH_DATE_PATTERN.finditer(line):
            found.append((match.group(0), _to_date(match.group(1), str(int(match.group(2)) + 1), match.group(3))))
    return [(literal, date) for literal, date in found if date is not None]


def _get_added_text(removed, written):
    # 置換前にもあった行は、今回書き加えた日付ではないため除く。
    removed_lines = {line.strip() for line in removed.splitlines()}
    return '\n'.join(line for line in written.splitlines() if line.strip() not in removed_lines)


def _find_time_bomb(definitions, today, added, content):
    # 戻り値は最初に見つけた (日付の文字列, 日付, 現在時刻の参照)。見つからなければNone。
    if any(pattern.search(content) for pattern in definitions['frozenClockPatterns']):
        return None
    clock = next((match.group(0) for pattern in definitions['clockPatterns'] if (match := pattern.search(content))), None)
    if clock is None:
        return None
    earliest = today - datetime.timedelta(days=definitions['pastDays'])
    latest = today + datetime.timedelta(days=definitions['futureDays'])
    for literal, date in _find_date_literals(added):
        if earliest <= date <= latest:
            return literal, date, clock
    return None


def _record_denial(state_path, path):
    # 戻り値は、このファイルを今回初めて拒否するか。同じファイルの拒否はセッションで1回だけにする。
    # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
    file_key = hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()
    return bool(_filter_first_time_denials(state_path, [(file_key, path)]))


def _parse_today(value):
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'invalid date: {value}') from None


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルと今日の日付を差し替えられるようにする。
    script_directory = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument('--definitions-path', default=os.path.join(script_directory, 'time-bomb-definitions.json'))
    parser.add_argument('--today', type=_parse_today, default=None)
    parsed_arguments = parser.parse_args(arguments)
    today = parsed_arguments.today or datetime.date.today()

    try:
        definitions = _load_definitions(parsed_arguments.definitions_path)
        test_file_patterns = _check_test_files['load_test_file_patterns']()

        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-time-bomb-literals', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        changes = extract_edit_tool_changes(tool_name, tool_args)

        real_cwd = os.path.realpath(cwd)
        candidates = []
        for change in changes:
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = _get_display_path(path, real_cwd)
            # テストファイルのパターンはOSによらず `/` 区切りのパスに対して照合する。
            if not _check_test_files['is_test_file'](test_file_patterns, display_path):
                continue
            current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
            # 上書きではファイル全体と現在の内容、それ以外では書き込む内容と置換前の内容を比べる。
            # 現在時刻の参照は、編集後のファイルにあればよいため、現在の内容と書き込む内容の両方から探す。
            if change['full_content'] is not None:
                added = _get_added_text(current, change['full_content'])
                content = change['full_content']
            else:
                added = _get_added_text(change['removed'], change['written'])
                content = current + '\n' + change['written']
            time_bomb = _find_time_bomb(definitions, today, added, content)
            if time_bomb:
                candidates.append((path, display_path, time_bomb))

        findings = []
        if candidates:
            for path, display_path, (literal, date, clock) in candidates:
                if _record_denial(state_path, path):
                    findings.append(_FINDING.format(file=display_path, literal=literal, date=date.isoformat(), clock=clock))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check time bomb literals: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = _DENY_REASON.format(findings='; '.join(findings), today=today.isoformat())
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
