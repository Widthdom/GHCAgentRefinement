import argparse
import io
import json
import os
import re
import runpy
import sqlite3
import sys
from typing import cast


_TEST_FILE_PATTERNS_KEY = 'testFilePatterns'
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


def _load_test_file_patterns(path):
    # テストファイルの判定はblock-test-weakening hookの定義を共有し、二重管理しない。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    patterns = definitions.get(_TEST_FILE_PATTERNS_KEY) if isinstance(definitions, dict) else None
    if not isinstance(patterns, list) or not patterns or not all(
        isinstance(pattern, str) and pattern.strip() for pattern in patterns
    ):
        raise ValueError(f'test file definitions require a non-empty array of non-empty strings for {_TEST_FILE_PATTERNS_KEY}.')
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {_TEST_FILE_PATTERNS_KEY}: {ex}') from None


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


def _is_test_file(test_file_patterns, display_path):
    # パターンはOSによらず `/` 区切りのパスに対して照合する。
    slash_path = display_path.replace(os.sep, '/')
    return any(pattern.search(slash_path) for pattern in test_file_patterns)


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


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    script_directory = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(
            os.path.dirname(script_directory), 'block-test-weakening', 'test-weakening-definitions.json',
        ),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        test_file_patterns = _load_test_file_patterns(parsed_arguments.definitions_path)
        # 記録側のpostToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(script_directory))),
            'utilities', 'assertion-failure-values-state', 'assertion-failure-values-state.py',
        ))

        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        state_path = state['get_state_path'](hook_input.get('sessionId'))
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
        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        changes = extract_edit_tool_changes(tool_name, hook_input['toolArgs'])

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
                if _is_test_file(test_file_patterns, display_path) or (
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
                current = (_read_existing(path) if os.path.isfile(path) else None) or ''
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
