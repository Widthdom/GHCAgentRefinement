import argparse
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
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_is_non_blank_string_list = runpy.run_path(os.path.join(
    _utilities_directory, 'check-string-list', 'check-string-list.py',
))['is_non_blank_string_list']
_truncate_text = runpy.run_path(os.path.join(_utilities_directory, 'truncate-text', 'truncate-text.py'))['truncate_text']
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']


_FIX_COMMIT_PATTERNS_KEY = 'fixCommitPatterns'
_GIT_TIMEOUT_SECONDS = 4
# gitの実行全体の期限。ファイルが多くても、hookの `timeoutSec`（10秒）より前に打ち切り、検査できない分は通す。
_GIT_DEADLINE = _git['create_deadline'](7)
# `git blame --porcelain` の各行の見出し（コミットID、元の行番号、現在の行番号、[まとまりの行数]）。
# SHA-1とSHA-256のリポジトリの両方を扱う。
_BLAME_HEADER_PATTERN = re.compile(r'^([0-9a-f]{40}|[0-9a-f]{64}) \d+ (\d+)(?: \d+)?$')
# 単語の文字を含まない行（`}`・`)`・空行など）は、どのコミットにもありうるため対象外とする。
_WORD_PATTERN = re.compile(r'\w')
_MAX_COMMITS_PER_FILE = 3
_MAX_DISPLAY_LENGTH = 60
_FINDING = '{file} line {lines} came from {commit} "{summary}" (run: git show {commit})'
_DENY_REASON = (
    'Edit denied: {findings}. These lines were introduced by commits that look like bug fixes, and this edit removes '
    'or rewrites them, which may bring the bug back. Read each commit with the command shown before changing the '
    'lines, then keep the fix or carry its intent over to the new code. This check keeps denying the edit until '
    'every listed commit has been viewed with `git show` or `git log` in this session.'
)


def _load_definitions(path):
    # 戻り値は、修正コミットのメッセージに一致する正規表現（コンパイル済み）のリスト。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    patterns = definitions.get(_FIX_COMMIT_PATTERNS_KEY) if isinstance(definitions, dict) else None
    if not _is_non_blank_string_list(patterns):
        raise ValueError(f'past fix definitions require a non-empty array of non-blank strings for {_FIX_COMMIT_PATTERNS_KEY}.')
    return _build_regex_matchers(patterns, _FIX_COMMIT_PATTERNS_KEY)


def _is_significant(line):
    return bool(_WORD_PATTERN.search(line))


def _find_removed_line_numbers(change, current):
    # 戻り値は、編集で消える現在のファイルの行番号（1始まり）の集合。書き込む内容に同じ行が残るものは除く。
    current_lines = current.splitlines()
    if change['full_content'] is not None:
        kept = {line.strip() for line in change['full_content'].splitlines()}
        return {number for number, line in enumerate(current_lines, 1) if _is_significant(line) and line.strip() not in kept}
    if change['replacement'] is not None:
        old, new = change['replacement']
        offset = current.find(old)
        if offset < 0:
            return set()
        kept = {line.strip() for line in new.splitlines()}
        first = current.count('\n', 0, offset) + 1
        return {
            first + index for index, line in enumerate(old.splitlines())
            if _is_significant(line) and line.strip() not in kept
        }
    # パッチは位置を特定できないため、削除行と同じ内容の行をすべて対象にする。
    kept = {line.strip() for line in change['written'].splitlines()}
    removed = {line.strip() for line in change['removed'].splitlines() if _is_significant(line)} - kept
    return {number for number, line in enumerate(current_lines, 1) if line.strip() in removed}


def _to_ranges(numbers):
    # 連続する行番号を `-L 開始,終了` の範囲にまとめる。
    ranges = []
    for number in sorted(numbers):
        if ranges and ranges[-1][1] == number - 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ranges


def _blame(path, line_numbers):
    # 戻り値は {行番号: コミットID}。まだコミットしていない行は含めない。
    arguments = ['blame', '--porcelain']
    for start, end in _to_ranges(line_numbers):
        arguments += ['-L', f'{start},{end}']
    # gitがない、git管理下でない、失敗した場合は検査しない。
    output = _git['get_git_output'](
        os.path.dirname(path), [*arguments, '--', os.path.basename(path)], _GIT_TIMEOUT_SECONDS, _GIT_DEADLINE,
    )
    if output is None:
        return {}
    commits = {}
    for line in output.splitlines():
        match = _BLAME_HEADER_PATTERN.match(line)
        if match and match.group(1).strip('0'):
            commits[int(match.group(2))] = match.group(1)
    return commits


def _get_messages(directory, commit_ids):
    # 戻り値は {コミットID: メッセージ全体}。区切りには本文に現れない制御文字（US・RS）を使う。
    output = _git['get_git_output'](
        directory, ['show', '-s', '--no-color', '--format=%H%x1f%B%x1e', *commit_ids], _GIT_TIMEOUT_SECONDS, _GIT_DEADLINE,
    )
    if output is None:
        return {}
    messages = {}
    for record in output.split('\x1e'):
        commit_id, separator, message = record.strip().partition('\x1f')
        if separator:
            messages[commit_id] = message.strip()
    return messages


def _find_past_fixes(patterns, viewed, path, display_path, line_numbers):
    # 戻り値は、まだ読んでいない修正コミットごとの指摘文のリスト。該当する行がなければ空。
    lines_by_commit = {}
    for number, commit_id in sorted(_blame(path, line_numbers).items()):
        if commit_id not in viewed:
            lines_by_commit.setdefault(commit_id, []).append(number)
    if not lines_by_commit:
        return []
    messages = _get_messages(os.path.dirname(path), list(lines_by_commit))
    findings = []
    for commit_id, numbers in lines_by_commit.items():
        message = messages.get(commit_id, '')
        if any(pattern.search(message) for pattern in patterns):
            findings.append(_FINDING.format(
                file=display_path, lines=', '.join(map(str, numbers)), commit=commit_id[:7],
                summary=_truncate_text(message.splitlines()[0], _MAX_DISPLAY_LENGTH),
            ))
    return findings[:_MAX_COMMITS_PER_FILE]


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    script_directory = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument('--definitions-path', default=os.path.join(script_directory, 'past-fix-definitions.json'))
    parsed_arguments = parser.parse_args(arguments)

    try:
        patterns = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input['read_hook_input']()
        # 読んだコミットは、記録側のpostToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            _utilities_directory, 'viewed-commits-state', 'viewed-commits-state.py',
        ))
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'edit-tool-changes', 'edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        changes = extract_edit_tool_changes(tool_name, tool_args)

        real_cwd = os.path.realpath(cwd)
        viewed = None
        findings = []
        for change in changes:
            path = os.path.realpath(os.path.join(cwd, change['path']))
            current = _read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None
            if not current:
                continue
            line_numbers = _find_removed_line_numbers(change, current)
            if not line_numbers:
                continue
            if viewed is None:
                viewed = state['get_viewed'](state_path)
            display_path = _get_display_path(path, real_cwd)
            findings.extend(_find_past_fixes(patterns, viewed, path, display_path, line_numbers))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check regression of past fixes: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = _DENY_REASON.format(findings='; '.join(findings))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
