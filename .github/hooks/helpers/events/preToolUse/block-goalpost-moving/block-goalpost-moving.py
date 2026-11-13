import io
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
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_truncate_text = runpy.run_path(os.path.join(_utilities_directory, 'truncate-text', 'truncate-text.py'))['truncate_text']
_check_test_files = runpy.run_path(os.path.join(_utilities_directory, 'check-test-files', 'check-test-files.py'))
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']


# 行の中のリテラル（文字列・数値・真偽値・null）。書き換え前後の行は、リテラル以外の部分が一致するものを対応させる。
_LITERAL_PATTERN = re.compile(
    r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`[^`\n]*`'
    r'|(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])'
    r'|\b(?:True|False|None|true|false|null|nil|undefined)\b'
)
_LITERAL_PLACEHOLDER = '\0'
_MAX_DISPLAY_LENGTH = 40
_FINDING = '{file} would replace the expected value {old} with {new}'
_DENY_REASON = (
    'Edit denied: {findings}, which is the actual value reported by the last failing test run. '
    'Changing the expectation to match the current output makes the test pass without fixing the behavior. '
    'Fix the implementation, or keep the test as it is and report the failure to the user. '
    'If the old expectation is genuinely wrong, for example when the user asked for the new behavior or the '
    'specification says so, retry the same edit; this check denies it only once per file.'
)


def _split_literals(line):
    return _LITERAL_PATTERN.sub(_LITERAL_PLACEHOLDER, line).strip(), _LITERAL_PATTERN.findall(line)


def _find_swap(state, pair_keys, removed, written):
    # リテラル以外が一致する書き換え前後の行の組で、同じ位置のリテラルが期待値から実際の値に変わったものを探す。
    # 戻り値は最初に見つけた (書き換え前の値, 書き換え後の値)。見つからなければNone。
    removed_by_skeleton = {}
    for line in set(removed.splitlines()):
        skeleton, literals = _split_literals(line)
        if literals:
            removed_by_skeleton.setdefault(skeleton, []).append(literals)
    for line in written.splitlines():
        skeleton, new_literals = _split_literals(line)
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
            pair_keys = {row[0] for row in connection.execute('SELECT pair FROM pairs')}
            if not pair_keys:
                return 0
            for change in changes:
                path = os.path.realpath(os.path.join(cwd, change['path']))
                # テストファイルの判定は、Windowsのnormcaseで小文字にする前のパスで行い、大文字小文字を区別できるようにする。
                display_path = _get_display_path(path, real_cwd)
                if not _check_test_files['is_test_file'](test_file_patterns, display_path):
                    continue
                # 上書きではファイル全体、それ以外では置換前の内容と比べる。
                current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
                if change['full_content'] is not None and current:
                    removed, written = current, change['full_content']
                else:
                    removed, written = change['removed'], change['written']
                swap = _find_swap(state, pair_keys, removed, written)
                # 同じファイルの拒否は1回だけにし、意図した編集なら再実行で通す。
                if swap and connection.execute(
                    'INSERT OR IGNORE INTO denials (file) VALUES (?)', (state['get_file_key'](path),),
                ).rowcount:
                    findings.append(_FINDING.format(
                        file=display_path,
                        old=_truncate_text(swap[0], _MAX_DISPLAY_LENGTH), new=_truncate_text(swap[1], _MAX_DISPLAY_LENGTH),
                    ))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check goalpost moving: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = _DENY_REASON.format(findings='; '.join(findings))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
