import io
import json
import os
import re
import runpy
import sqlite3
import sys
from typing import cast


_SHELL_TOOL_NAMES = {'bash', 'powershell'}
# 行頭の装飾（pytestの `E`、差分の `-`・`+`）を許容する。
_LINE_PREFIX = r'^\s*(?:E\s+)?[-+]?\s*'
# 期待値の行と、その直後3行以内の実際の値の行（Jest・xUnit・NUnit・AssertJ・testifyなど）。
_EXPECTED_LINE_PATTERN = re.compile(_LINE_PREFIX + r'expected(?:\s+value)?\s*:\s*(.+?)\s*$', re.IGNORECASE)
_ACTUAL_LINE_PATTERN = re.compile(
    _LINE_PREFIX + r'(?:received|actual|but\s+was)(?:\s+value)?\s*:\s*(.+?)\s*$', re.IGNORECASE,
)
_ACTUAL_LINE_DISTANCE = 3
# 1行に期待値と実際の値が並ぶ形式。グループは (期待値, 実際の値) の順に並べ替えて扱う。
#   JUnit：expected:<4> but was:<3>
#   Go：got 3, want 4 / want 4, got 3
_INLINE_PATTERNS = (
    (re.compile(r'expected:\s*<(.+?)>\s*but\s+was:\s*<(.+?)>', re.IGNORECASE), False),
    (re.compile(r'\bwant:?\s+(.+?),?\s+got:?\s+(.+?)\s*$', re.IGNORECASE), False),
    (re.compile(r'\bgot:?\s+(.+?),?\s+want:?\s+(.+?)\s*$', re.IGNORECASE), True),
)
# どちらが期待値か出力からは決まらない形式。両方の向きを記録する。
#   unittest：AssertionError: 3 != 4
#   pytest：E       assert 3 == 4
_SYMMETRIC_PATTERNS = (
    re.compile(r'^\s*(?:E\s+)?AssertionError:\s*(.+?)\s+!=\s+(.+?)\s*$'),
    re.compile(r'^\s*E\s+assert\s+(.+?)\s+==\s+(.+?)\s*$'),
)
# Rustのassert_eq!：left: 3 / right: 4（旧形式は left: `3`, / right: `4`）。
_LEFT_LINE_PATTERN = re.compile(r'^\s*left:\s*(.+?)\s*$')
_RIGHT_LINE_PATTERN = re.compile(r'^\s*right:\s*(.+?)\s*$')
_MAX_PAIRS = 100


def _find_following(lines, start, pattern):
    for line in lines[start + 1:start + 1 + _ACTUAL_LINE_DISTANCE]:
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def _parse_failure_values(text):
    # 戻り値は、テストの出力から読み取った (期待値, 実際の値) の組の集合（正規化前）。
    pairs = set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _EXPECTED_LINE_PATTERN.match(line)
        if match:
            actual = _find_following(lines, index, _ACTUAL_LINE_PATTERN)
            if actual is not None:
                pairs.add((match.group(1), actual))
        for pattern, reversed_groups in _INLINE_PATTERNS:
            for found in pattern.finditer(line):
                first, second = found.groups()
                pairs.add((second, first) if reversed_groups else (first, second))
        for pattern in _SYMMETRIC_PATTERNS:
            match = pattern.match(line)
            if match:
                pairs.update({match.groups(), match.groups()[::-1]})
        match = _LEFT_LINE_PATTERN.match(line)
        if match:
            right = _find_following(lines, index, _RIGHT_LINE_PATTERN)
            if right is not None:
                pairs.update({(match.group(1), right), (right, match.group(1))})
    return pairs


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        # テストの実行かどうかの判定は、共通utilityで行う。
        is_test_command = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'check-test-runs', 'check-test-runs.py',
        ))['is_test_command']
        # 拒否側のpreToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'assertion-failure-values-state', 'assertion-failure-values-state.py',
        ))

        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')

        state_path = state['get_state_path'](hook_input.get('sessionId'))
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        if tool_name.removeprefix('functions.') not in _SHELL_TOOL_NAMES:
            return 0
        tool_args = hook_input['toolArgs']
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except ValueError:
                return 0
        command = tool_args.get('command') if isinstance(tool_args, dict) else None
        if not isinstance(command, str) or not is_test_command(command):
            return 0

        tool_result = hook_input.get('toolResult')
        output = tool_result.get('textResultForLlm') if isinstance(tool_result, dict) else None
        pairs = set()
        for expected, actual in _parse_failure_values(output if isinstance(output, str) else ''):
            expected, actual = state['normalize_value'](expected), state['normalize_value'](actual)
            if expected is not None and actual is not None and expected != actual:
                pairs.add((expected, actual))
        pairs = sorted(pairs)[:_MAX_PAIRS]
        keys = [state['get_pair_key'](expected, actual) for expected, actual in pairs]
        expected_keys = {state['get_value_key'](expected) for expected, _ in pairs}

        # 記録がなく、消す記録もなければ状態ファイルを作らない。
        if not keys and not os.path.isfile(state_path):
            return 0
        # 最新のテスト実行の結果だけを残す。成功した実行や読み取れない出力では、前の失敗の記録を消す。
        with state['open_state'](state_path) as connection:
            connection.execute('DELETE FROM pairs')
            connection.execute('DELETE FROM expected_values')
            connection.executemany('INSERT OR IGNORE INTO pairs (pair) VALUES (?)', [(key,) for key in keys])
            connection.executemany(
                'INSERT OR IGNORE INTO expected_values (value) VALUES (?)', [(key,) for key in expected_keys],
            )
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to record test failure values: {ex}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
