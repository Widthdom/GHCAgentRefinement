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


SHELL_TOOL_NAMES = {'bash', 'powershell'}
# テスト実行とみなすコマンド。verify-completion-claims hookと同じ基準で、`latest` などの語中は除く。
TEST_COMMAND_PATTERN = re.compile(
    r'(?<![a-z0-9])(?:tests?|pytest|unittest|jest|vitest|mocha|rspec|phpunit|ctest|tox|pester)(?![a-z])',
    re.IGNORECASE,
)
ANSI_ESCAPE_PATTERN = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')

# 件数の集計行。各関数は (実行した件数, スキップした件数) を返す。
#   pytest：`=== 3 passed, 1 skipped in 0.12s ===`・`3 passed in 0.12s`（-q）
PYTEST_SUMMARY_PATTERN = re.compile(
    r'^=*\s*((?:\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?|rerun)(?:, )?)+)'
    r'\s+in\s+[\d.]+s\b'
)
PYTEST_ITEM_PATTERN = re.compile(r'(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)')
#   unittest：`Ran 3 tests in 0.001s` と `OK (skipped=1)`
UNITTEST_RAN_PATTERN = re.compile(r'^Ran (\d+) tests? in\b')
UNITTEST_SKIPPED_PATTERN = re.compile(r'^(?:OK|FAILED) \(.*?\bskipped=(\d+)')
#   Jest：`Tests:       1 failed, 1 skipped, 3 passed, 5 total`
JEST_SUMMARY_PATTERN = re.compile(r'^Tests:\s+(.*?)(\d+) total\s*$')
#   Vitest：`Tests  3 passed | 1 skipped (4)`
VITEST_SUMMARY_PATTERN = re.compile(r'^\s*Tests\s+(.*?)\s*\((\d+)\)\s*$')
SKIPPED_ITEM_PATTERN = re.compile(r'(\d+) (?:skipped|todo|pending)')
#   Mocha：`3 passing (12ms)`・`1 failing`・`1 pending`
MOCHA_PATTERN = re.compile(r'^\s*(\d+) (passing|failing|pending)\b')
#   RSpec：`5 examples, 1 failure, 2 pending`
RSPEC_PATTERN = re.compile(r'^(\d+) examples?, \d+ failures?(?:, (\d+) pending)?')
#   cargo test：`test result: ok. 3 passed; 0 failed; 1 ignored; ...`（テストバイナリごと）
CARGO_PATTERN = re.compile(r'^test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored')
#   dotnet test：`Passed!  - Failed:     0, Passed:     5, Skipped:     1, Total:     6`（テストプロジェクトごと）
DOTNET_PATTERN = re.compile(r'\bFailed:\s*(\d+),\s*Passed:\s*(\d+),\s*Skipped:\s*(\d+),\s*Total:\s*(\d+)')
#   PHPUnit：`OK (3 tests, 5 assertions)`・`Tests: 4, Assertions: 6, Failures: 1, Skipped: 1.`
PHPUNIT_OK_PATTERN = re.compile(r'^OK \((\d+) tests?,')
PHPUNIT_SUMMARY_PATTERN = re.compile(r'^Tests: (\d+), Assertions: \d+')
PHPUNIT_SKIPPED_PATTERN = re.compile(r'\bSkipped: (\d+)')
#   Pester：`Tests Passed: 3, Failed: 0, Skipped: 1 NotRun: 0`
PESTER_PATTERN = re.compile(r'^Tests Passed: (\d+), Failed: (\d+), Skipped: (\d+)')
#   go test：`ok  	example.com/pkg	0.01s`・`ok  	example.com/pkg	(cached) [no tests to run]`（パッケージごと）
GO_PACKAGE_PATTERN = re.compile(r'^(?:ok|FAIL)\s+\S+\s+(?:[\d.]+s|\(cached\))(.*)$')

# 件数の集計行がない場合に、テストが1件も実行されなかったと分かる出力。
NO_TESTS_PATTERNS = (
    re.compile(r'\bno tests ran\b'),                                     # pytest
    re.compile(r'\bcollected 0 items\b'),                                # pytest
    re.compile(r'\bNo tests found\b'),                                   # Jest
    re.compile(r'\bNo test files found\b'),                              # Vitest
    re.compile(r'\bNo test matches the given testcase filter\b'),        # dotnet test
    re.compile(r'\bNo test is available in\b'),                          # dotnet test
    re.compile(r'\btesting: warning: no tests to run\b'),                # go test
    re.compile(r'\bNo tests executed\b'),                                # PHPUnit
)

MAX_COMMAND_LENGTH = 200
ZERO_CONTEXT = (
    'The test command `{command}` ran no tests ({detail}). A filter, path, pattern, or configuration '
    'probably matched nothing, so this run verifies nothing even if it exited successfully. '
    'Fix the selection and re-run so that the intended tests actually execute, '
    'and do not report this run as passing tests.'
)
DECREASE_CONTEXT = (
    'The test command `{command}` {changes} compared with the last time the same command ran in this session. '
    'If tests were deleted, skipped, or deselected to make the run pass, restore them and fix the cause instead. '
    'If the change is intended (for example, the user asked to remove those tests), '
    'state it explicitly in the final response.'
)


def count_tests(text):
    # 戻り値は (実行した件数, スキップした件数)。件数の集計行がなければ (None, None)。
    # 1回のコマンドで複数のテストを実行した場合（`test-all-hooks.py`・`cargo test`・`dotnet test` など）は合計する。
    executed = 0
    skipped = 0
    found = False
    for raw_line in text.splitlines():
        line = ANSI_ESCAPE_PATTERN.sub('', raw_line).rstrip()
        if match := PYTEST_SUMMARY_PATTERN.match(line):
            for number, kind in PYTEST_ITEM_PATTERN.findall(match.group(1)):
                if kind in ('skipped', 'deselected'):
                    skipped += int(number)
                else:
                    executed += int(number)
            found = True
        elif match := UNITTEST_RAN_PATTERN.match(line):
            executed += int(match.group(1))
            found = True
        elif match := UNITTEST_SKIPPED_PATTERN.match(line):
            # `Ran N tests` のNはスキップを含むため、実行した件数から除く。
            executed -= int(match.group(1))
            skipped += int(match.group(1))
        elif match := (JEST_SUMMARY_PATTERN.match(line) or VITEST_SUMMARY_PATTERN.match(line)):
            not_run = sum(int(number) for number in SKIPPED_ITEM_PATTERN.findall(match.group(1)))
            executed += int(match.group(2)) - not_run
            skipped += not_run
            found = True
        elif match := MOCHA_PATTERN.match(line):
            if match.group(2) == 'pending':
                skipped += int(match.group(1))
            else:
                executed += int(match.group(1))
            found = True
        elif match := RSPEC_PATTERN.match(line):
            pending = int(match.group(2) or 0)
            executed += int(match.group(1)) - pending
            skipped += pending
            found = True
        elif match := CARGO_PATTERN.match(line):
            executed += int(match.group(1)) + int(match.group(2))
            skipped += int(match.group(3))
            found = True
        elif match := DOTNET_PATTERN.search(line):
            executed += int(match.group(1)) + int(match.group(2))
            skipped += int(match.group(3))
            found = True
        elif match := (PHPUNIT_OK_PATTERN.match(line) or PHPUNIT_SUMMARY_PATTERN.match(line)):
            not_run = PHPUNIT_SKIPPED_PATTERN.search(line)
            not_run = int(not_run.group(1)) if not_run else 0
            executed += int(match.group(1)) - not_run
            skipped += not_run
            found = True
        elif match := PESTER_PATTERN.match(line):
            executed += int(match.group(1)) + int(match.group(2))
            skipped += int(match.group(3))
            found = True
        elif match := GO_PACKAGE_PATTERN.match(line):
            # goは件数を出さないため、テストを実行したパッケージを1件と数える。
            # `[no test files]` はテストのないパッケージで、`./...` では普通に現れるため数えない。
            if 'no tests to run' not in match.group(1) and 'no test files' not in match.group(1):
                executed += 1
            found = True
    if not found:
        return None, None
    return max(executed, 0), skipped


def get_no_tests_detail(text):
    for pattern in NO_TESTS_PATTERNS:
        if match := pattern.search(ANSI_ESCAPE_PATTERN.sub('', text)):
            return f'output: "{match.group(0)}"'
    return None


def get_state_path(session_id):
    # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。DBファイル自体は自動削除しない。
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('sessionId must be a non-empty UUID string.')
    try:
        parsed_session_id = uuid.UUID(session_id.strip())
    except ValueError:
        raise ValueError('sessionId must be a valid UUID.') from None
    return os.path.join(
        tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'test-counts', f'{parsed_session_id}.sqlite3',
    )


def record_counts(state_path, command, executed, skipped):
    # 同じコマンドの前回の件数を返し、今回の件数で置き換える。コマンドは保存せず、照合用のSHA-256だけを保存する。
    # SQLiteのトランザクションで、並列のツール呼び出しでも状態の更新を取りこぼさない。
    command_key = hashlib.sha256(command.encode('utf-8')).hexdigest()
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS counts ('
            'command TEXT PRIMARY KEY, executed INTEGER NOT NULL, skipped INTEGER NOT NULL)'
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = connection.execute(
                'SELECT executed, skipped FROM counts WHERE command = ?', (command_key,),
            ).fetchone()
            connection.execute(
                'INSERT OR REPLACE INTO counts (command, executed, skipped) VALUES (?, ?, ?)',
                (command_key, executed, skipped),
            )
    return previous


def get_context(command, executed, skipped, previous, no_tests_detail):
    display_command = command if len(command) <= MAX_COMMAND_LENGTH else command[:MAX_COMMAND_LENGTH] + '...'
    previous_executed, previous_skipped = previous if previous is not None else (None, None)
    # 0件は、同じコマンドで前回も0件だった場合は通知済みとして繰り返さない。
    if executed == 0 and previous_executed != 0:
        detail = no_tests_detail or ('0 tests executed' if not skipped else f'0 tests executed, {skipped} skipped')
        return ZERO_CONTEXT.format(command=display_command, detail=detail)
    if executed == 0 or previous is None:
        return None
    changes = []
    if executed < previous_executed:
        changes.append(f'ran {executed} tests instead of {previous_executed}')
    if skipped > previous_skipped:
        changes.append(f'skipped {skipped} tests instead of {previous_skipped}')
    if not changes:
        return None
    return DECREASE_CONTEXT.format(command=display_command, changes=' and '.join(changes))


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')

        state_path = get_state_path(hook_input.get('sessionId'))
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        if tool_name.removeprefix('functions.') not in SHELL_TOOL_NAMES:
            return 0
        tool_args = hook_input['toolArgs']
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except ValueError:
                return 0
        command = tool_args.get('command') if isinstance(tool_args, dict) else None
        if not isinstance(command, str) or not TEST_COMMAND_PATTERN.search(command):
            return 0
        command = command.strip()

        tool_result = hook_input.get('toolResult')
        output = tool_result.get('textResultForLlm') if isinstance(tool_result, dict) else None
        output = output if isinstance(output, str) else ''
        executed, skipped = count_tests(output)
        no_tests_detail = get_no_tests_detail(output)
        if executed is None:
            # 件数の集計行がなく、0件と分かる出力もなければ判断できない。記録も変えない。
            if no_tests_detail is None:
                return 0
            executed, skipped = 0, 0

        previous = record_counts(state_path, command, executed, skipped)
        context = get_context(command, executed, skipped, previous, no_tests_detail)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to detect vacuous test success: {ex}', file=sys.stderr)
        return 2

    if context is not None:
        # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({'additionalContext': context}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
