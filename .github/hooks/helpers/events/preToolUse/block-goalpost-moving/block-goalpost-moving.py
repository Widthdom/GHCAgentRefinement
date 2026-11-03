import argparse
import io
import json
import os
import re
import runpy
import sqlite3
import sys
from typing import cast


TEST_FILE_PATTERNS_KEY = 'testFilePatterns'
# 行の中のリテラル（文字列・数値・真偽値・null）。書き換え前後の行は、リテラル以外の部分が一致するものを対応させる。
LITERAL_PATTERN = re.compile(
    r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`[^`\n]*`'
    r'|(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])'
    r'|\b(?:True|False|None|true|false|null|nil|undefined)\b'
)
LITERAL_PLACEHOLDER = '\0'
MAX_DISPLAY_LENGTH = 40
FINDING = '{file} would replace the expected value {old} with {new}'
DENY_REASON = (
    'Edit denied: {findings}, which is the actual value reported by the last failing test run. '
    'Changing the expectation to match the current output makes the test pass without fixing the behavior. '
    'Fix the implementation, or keep the test as it is and report the failure to the user. '
    'If the old expectation is genuinely wrong, for example when the user asked for the new behavior or the '
    'specification says so, retry the same edit; this check denies it only once per file.'
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


def read_existing(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()
    except OSError:
        return None


def get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path


def is_test_file(test_file_patterns, display_path):
    # パターンはOSによらず `/` 区切りのパスに対して照合する。
    slash_path = display_path.replace(os.sep, '/')
    return any(pattern.search(slash_path) for pattern in test_file_patterns)


def split_literals(line):
    return LITERAL_PATTERN.sub(LITERAL_PLACEHOLDER, line).strip(), LITERAL_PATTERN.findall(line)


def find_swap(state, pair_keys, removed, written):
    # リテラル以外が一致する書き換え前後の行の組で、同じ位置のリテラルが期待値から実際の値に変わったものを探す。
    # 戻り値は最初に見つけた (書き換え前の値, 書き換え後の値)。見つからなければNone。
    removed_by_skeleton = {}
    for line in set(removed.splitlines()):
        skeleton, literals = split_literals(line)
        if literals:
            removed_by_skeleton.setdefault(skeleton, []).append(literals)
    for line in written.splitlines():
        skeleton, new_literals = split_literals(line)
        for old_literals in removed_by_skeleton.get(skeleton, []):
            if len(old_literals) != len(new_literals):
                continue
            for old, new in zip(old_literals, new_literals):
                if old == new:
                    continue
                old_value, new_value = state['normalize_value'](old), state['normalize_value'](new)
                if old_value is not None and new_value is not None and (
                    state['get_pair_key'](old_value, new_value) in pair_keys
                ):
                    return old, new
    return None


def shorten(value):
    return value if len(value) <= MAX_DISPLAY_LENGTH else value[:MAX_DISPLAY_LENGTH] + '...'


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

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
        test_file_patterns = load_test_file_patterns(parsed_arguments.definitions_path)
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
            pair_keys = {row[0] for row in connection.execute('SELECT pair FROM pairs')}
            if not pair_keys:
                return 0
            for change in changes:
                path = os.path.realpath(os.path.join(cwd, change['path']))
                # テストファイルの判定は、Windowsのnormcaseで小文字にする前のパスで行い、大文字小文字を区別できるようにする。
                display_path = get_display_path(path, real_cwd)
                if not is_test_file(test_file_patterns, display_path):
                    continue
                # 上書きではファイル全体、それ以外では置換前の内容と比べる。
                current = (read_existing(path) if os.path.isfile(path) else None) or ''
                if change['full_content'] is not None and current:
                    removed, written = current, change['full_content']
                else:
                    removed, written = change['removed'], change['written']
                swap = find_swap(state, pair_keys, removed, written)
                # 同じファイルの拒否は1回だけにし、意図した編集なら再実行で通す。
                if swap and connection.execute(
                    'INSERT OR IGNORE INTO denials (file) VALUES (?)', (state['get_file_key'](path),),
                ).rowcount:
                    findings.append(FINDING.format(file=display_path, old=shorten(swap[0]), new=shorten(swap[1])))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check goalpost moving: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = DENY_REASON.format(findings='; '.join(findings))
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
