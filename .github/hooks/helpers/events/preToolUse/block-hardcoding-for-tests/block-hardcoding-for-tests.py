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
_check_test_files = runpy.run_path(os.path.join(_utilities_directory, 'check-test-files', 'check-test-files.py'))
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']


# コードではなく文章のファイル。説明用のコード例を書く編集は拒否しない。
_DOCUMENT_EXTENSIONS = {'.md', '.markdown', '.txt', '.rst', '.adoc'}
# 行の中のリテラル（文字列・数値・真偽値・null）。block-goalpost-moving hookと同じ基準。
_LITERAL_PATTERN = re.compile(
    r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`[^`\n]*`'
    r'|(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])'
    r'|\b(?:True|False|None|true|false|null|nil|undefined)\b'
)
# 条件分岐の始まり。リテラルを伏せた行に対して照合する。
_CONDITION_KEYWORD_PATTERN = re.compile(r'(?<![\w.])(?:if|elif|elsif|else\s+if|unless|when|case)\b')
# 条件分岐の行で、条件の後に続く処理の始まり。
_BODY_START_PATTERN = re.compile(r'\breturn\b|\byield\b|=>|\bthen\b|:(?=\s*\S)|\)\s*\{?\s*(?=[\w"\'`-])')
# キーワードのない三項演算子（`x == 1 ? 4 : y`）。比較から `?` までを条件とする。
_TERNARY_PATTERN = re.compile(r'(?:===?|!==?)[^?\n]*\?(?!\?)')
# 条件分岐の行の後で、処理とみなす行数。
_BODY_LINE_DISTANCE = 3
# 処理の範囲を打ち切る行（次の分岐）。
_BODY_END_PATTERN = re.compile(r'^\s*(?:\}\s*)?(?:else|elif|elsif|case|when|default)\b')
_MAX_DISPLAY_LENGTH = 40
_FINDING = '{file} would add a branch on {condition} that yields the expected value {value}'
_DENY_REASON = (
    'Edit denied: {findings}, which is the expected value of the last failing test run. '
    'A branch that returns the expected value only for specific inputs makes the test pass without implementing '
    'the behavior. Implement the general logic instead, or report the failure to the user. '
    'If this branch is a genuine rule of the specification, for example a base case or a documented special value, '
    'retry the same edit; this check denies it only once per file.'
)


def _mask_literals(line):
    # リテラルの中の `:` や `if` を構文と誤認しないよう、同じ長さの空白で伏せる。
    return _LITERAL_PATTERN.sub(lambda match: ' ' * len(match.group()), line)


def _split_condition(line):
    # 条件分岐の行なら (条件の始まり, 処理の始まり) の位置を返す。処理が次の行からならその位置は行末。
    masked = _mask_literals(line)
    match = _CONDITION_KEYWORD_PATTERN.search(masked)
    if match:
        body = _BODY_START_PATTERN.search(masked, match.end())
        return match.end(), body.start() if body else len(line)
    match = _TERNARY_PATTERN.search(masked)
    if match:
        return match.start(), match.end()
    return None


def _literals_between(line, start, end):
    return [match.group() for match in _LITERAL_PATTERN.finditer(line) if start <= match.start() < end]


def _find_hardcoding(state, expected_keys, lines, added):
    # 追加する条件分岐のうち、条件がリテラルとの比較で、処理が期待値のリテラルを返すものを探す。
    # 戻り値は最初に見つけた (条件のリテラル, 期待値のリテラル)。見つからなければNone。
    def is_expected(literal):
        value = state['normalize_value'](literal)
        return value is not None and state['get_value_key'](value) in expected_keys

    for index, line in enumerate(lines):
        if index not in added:
            continue
        split = _split_condition(line)
        if split is None:
            continue
        condition_literals = _literals_between(line, *split)
        if not condition_literals:
            continue
        body_literals = _literals_between(line, split[1], len(line))
        for body_line in lines[index + 1:index + 1 + _BODY_LINE_DISTANCE]:
            if _BODY_END_PATTERN.match(body_line) or _split_condition(body_line):
                break
            body_literals.extend(_LITERAL_PATTERN.findall(body_line))
        for literal in body_literals:
            if is_expected(literal):
                return condition_literals[0], literal
    return None


def _shorten(value):
    return value if len(value) <= _MAX_DISPLAY_LENGTH else value[:_MAX_DISPLAY_LENGTH] + '...'


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        test_file_patterns = _check_test_files['load_test_file_patterns']()
        # 記録側のpostToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            _utilities_directory, 'assertion-failure-values-state', 'assertion-failure-values-state.py',
        ))

        hook_input = _hook_input['read_hook_input']()
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        real_cwd = os.path.realpath(cwd)
        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        changes = extract_edit_tool_changes(tool_name, tool_args)

        # テストの失敗を記録していなければ、状態ファイルを作らずに許可する。
        if not changes or not os.path.isfile(state_path):
            return 0

        findings = []
        with state['open_state'](state_path) as connection:
            expected_keys = {row[0] for row in connection.execute('SELECT value FROM expected_values')}
            if not expected_keys:
                return 0
            for change in changes:
                path = os.path.realpath(os.path.join(cwd, change['path']))
                # テストファイルの判定は、Windowsのnormcaseで小文字にする前のパスで行い、大文字小文字を区別できるようにする。
                display_path = _get_display_path(path, real_cwd)
                if _check_test_files['is_test_file'](test_file_patterns, display_path) or (
                    os.path.splitext(path)[1].lower() in _DOCUMENT_EXTENSIONS
                ):
                    continue
                # パッチでは処理の行を判定できるよう文脈行（` `）も含めて並べ、追加行（`+`）の番号を記録する。
                # 上書きではファイル全体、それ以外では置換前の内容にない行を追加行とする。
                lines, added, removed = change['written'].splitlines(), None, change['removed']
                if change['patch_lines'] is not None:
                    body = [(kind, line) for kind, line in change['patch_lines'] if kind != '-']
                    lines = [line for _, line in body]
                    added = {index for index, (kind, _) in enumerate(body) if kind == '+'}
                current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
                if change['full_content'] is not None:
                    lines, added, removed = change['full_content'].splitlines(), None, current
                if added is None:
                    existing_lines = {line.strip() for line in removed.splitlines()}
                    added = {index for index, line in enumerate(lines) if line.strip() not in existing_lines}
                hardcoding = _find_hardcoding(state, expected_keys, lines, added)
                # 同じファイルの拒否は1回だけにし、意図した編集なら再実行で通す。
                if hardcoding and connection.execute(
                    'INSERT OR IGNORE INTO hardcoding_denials (file) VALUES (?)', (state['get_file_key'](path),),
                ).rowcount:
                    findings.append(_FINDING.format(
                        file=display_path, condition=_shorten(hardcoding[0]), value=_shorten(hardcoding[1]),
                    ))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check hardcoding for tests: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = _DENY_REASON.format(findings='; '.join(findings))
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
