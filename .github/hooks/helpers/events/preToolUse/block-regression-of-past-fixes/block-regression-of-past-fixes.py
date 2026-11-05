import argparse
import io
import json
import os
import re
import runpy
import sqlite3
import subprocess
import sys
from typing import cast


_FIX_COMMIT_PATTERNS_KEY = 'fixCommitPatterns'
_GIT_TIMEOUT_SECONDS = 4
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
    if not isinstance(patterns, list) or not patterns or not all(
        isinstance(pattern, str) and pattern.strip() for pattern in patterns
    ):
        raise ValueError(f'past fix definitions require a non-empty array of non-empty strings for {_FIX_COMMIT_PATTERNS_KEY}.')
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {_FIX_COMMIT_PATTERNS_KEY}: {ex}') from None


def _read_existing(path):
    # 改行コードは `\n` に揃えて読み、CRLFのファイルでも置換前の内容（`\n` 区切り）を探せるようにする。
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()
    except OSError:
        return None


def _get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示す。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path.replace(os.sep, '/')


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


def _run_git(directory, arguments):
    # 戻り値は標準出力の文字列。gitがない、git管理下でない、失敗した場合はNoneを返して検査しない。
    try:
        result = subprocess.run(
            ['git', '-C', directory, *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=_GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode('utf-8', errors='replace')


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
    output = _run_git(os.path.dirname(path), [*arguments, '--', os.path.basename(path)])
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
    output = _run_git(directory, ['show', '-s', '--no-color', '--format=%H%x1f%B%x1e', *commit_ids])
    if output is None:
        return {}
    messages = {}
    for record in output.split('\x1e'):
        commit_id, separator, message = record.strip().partition('\x1f')
        if separator:
            messages[commit_id] = message.strip()
    return messages


def _shorten(value):
    return value if len(value) <= _MAX_DISPLAY_LENGTH else value[:_MAX_DISPLAY_LENGTH] + '...'


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
                summary=_shorten(message.splitlines()[0]),
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

        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        # 読んだコミットは、記録側のpostToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(script_directory))),
            'utilities', 'viewed-commits-state', 'viewed-commits-state.py',
        ))
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

        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(script_directory))),
            'utilities', 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        changes = extract_edit_tool_changes(tool_name, hook_input['toolArgs'])

        real_cwd = os.path.realpath(cwd)
        viewed = None
        findings = []
        for change in changes:
            path = os.path.realpath(os.path.join(cwd, change['path']))
            current = _read_existing(path) if os.path.isfile(path) else None
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
        # SDK形式のトップレベルpermissionDecision。正常な拒否なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({
            'permissionDecision': 'deny',
            'permissionDecisionReason': reason,
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
