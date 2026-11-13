import io
import os
import re
import runpy
import shlex
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
_tool_kinds = runpy.run_path(os.path.join(_utilities_directory, 'check-tool-kinds', 'check-tool-kinds.py'))
_get_text_sha256_digest = runpy.run_path(os.path.join(
    _utilities_directory, 'get-sha256-digest', 'get-sha256-digest.py',
))['get_text_sha256_digest']
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


# コマンドの区切り。`2>&1` の `&` は区切りになるが、前後がgitのコマンドにならないため影響しない。
_SEPARATORS = {';', '&', '&&', '|', '||', '\n'}
# 値を取るgitのグローバルオプション。
_GIT_OPTIONS_WITH_VALUE = {'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path', '--config-env'}
# 変更をインデックスに載せるgitのサブコマンド。同じコマンドでコミットより前にあれば、作業ツリーの変更もコミットに入りうる。
_STAGING_SUBCOMMANDS = {'add', 'rm', 'mv', 'stage'}
# 値を取るcommitのオプション（メッセージとファイル以外）。値を読み飛ばすために使う。
_COMMIT_OPTIONS_WITH_VALUE = {'--author', '--date', '--cleanup', '--trailer', '--pathspec-from-file'}
# メッセージを他のコミットから流用するオプション。メッセージを書いていないため対象外とする。
_REUSE_MESSAGE_OPTIONS = {'-C', '-c', '--reuse-message', '--reedit-message', '--fixup', '--squash'}
# ヒアドキュメントの開始。区切りの語と、同じ行の残り（`&& git push` など）を取り出す。
_HEREDOC_PATTERN = re.compile(r'<<-?[ \t]*([\'"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n')
# `-m "$(cat <<'EOF' ... EOF)"` の形。ヒアドキュメントの本文を取り除くと `$(cat` から始まる値が残る。
_COMMAND_SUBSTITUTION_PATTERN = re.compile(r'^\s*\$\(\s*cat\b')
# 空のツリー。親のないコミットとの差分に使う。
_EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
_INLINE_CODE_PATTERN = re.compile(r'`([^`\n]+)`')
_URL_PATTERN = re.compile(r'\b[a-z][a-z0-9+.-]*://\S+', re.IGNORECASE)
# 引用符で囲まずに書いたファイル名。URLやメールアドレスのドメインを拾わないよう、拡張子を限る。
_BARE_FILE_PATTERN = re.compile(
    r'(?<![\w./@-])[\w./-]*\w\.(?:py|cs|csproj|sln|ps1|psm1|sh|cmd|bat|js|mjs|cjs|ts|tsx|jsx|json|jsonl|md|ya?ml|toml|xml|'
    r'go|rs|java|kt|rb|php|c|cc|cpp|h|hpp|css|scss|html|sql|txt|ini|cfg)(?![\w-])',
    re.IGNORECASE,
)
# 直後の否定の判定は、テストが通ったという主張と同じ基準（check-test-runs utility）を使う。
_JAPANESE_NEGATION = runpy.run_path(os.path.join(_utilities_directory, 'check-test-runs', 'check-test-runs.py'))['JAPANESE_NEGATION']
_TEST_ADDITION_PATTERNS = (
    re.compile(r'テスト[^。\n]{0,10}?(?:追加|作成|新設|書い|書き)' + _JAPANESE_NEGATION),
    re.compile(r'\b(?:add|added|adds|adding|write|wrote|writes|create|created|creates)\s+(?:\w+\s+){0,3}?(?:unit\s+)?(?:tests?|specs?)\b', re.IGNORECASE),
)
_TEST_PATH_PATTERN = re.compile(r'test|spec', re.IGNORECASE)
_MAX_LISTED_TOKENS = 10
_MAX_UNTRACKED_FILE_BYTES = 1024 * 1024
# gitの実行全体の期限。hookの `timeoutSec`（10秒）より前に打ち切り、判定できなければコミットを通す。
_GIT_DEADLINE = _git['create_deadline'](7)
_DENY_REASON_PREFIX = 'Commit denied: the commit message describes changes that are not in the commit'


def _split_heredoc(command):
    # 最初のヒアドキュメントの本文を取り除いたコマンドと、本文を返す。本文がなければNone。
    match = _HEREDOC_PATTERN.search(command)
    if match is None:
        return command, None
    delimiter = match.group(2)
    head = command[:match.start()] + match.group(3)
    lines = command[match.end():].split('\n')
    for index, line in enumerate(lines):
        if line.strip() == delimiter:
            return head + '\n' + '\n'.join(lines[index + 1:]), '\n'.join(lines[:index]) + '\n'
    return head, '\n'.join(lines)


def _split_segments(command):
    # 引用符を解釈して語に分け、区切りごとのコマンドのリストを返す。引用符が閉じていなければValueError。
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    lexer.commenters = ''
    segments = [[]]
    for token in lexer:
        if token in _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _parse_git(segment, cwd):
    # gitのコマンドなら (サブコマンド, 残りの引数, gitを実行するディレクトリ) を返す。gitでなければNone。
    if not segment or os.path.basename(segment[0]).lower() not in ('git', 'git.exe'):
        return None
    index = 1
    while index < len(segment) and segment[index].startswith('-'):
        option = segment[index]
        if option in _GIT_OPTIONS_WITH_VALUE and index + 1 < len(segment):
            if option == '-C':
                cwd = os.path.join(cwd, os.path.expanduser(segment[index + 1]))
            index += 2
        else:
            index += 1
    if index >= len(segment):
        return None
    return segment[index], segment[index + 1:], cwd


def _parse_commit(arguments, cwd, heredoc):
    # commitの引数から {message, all, amend, pathspec} を返す。メッセージを読めない場合はNone。
    messages = []
    message_file = None
    commit = {'all': False, 'amend': False, 'pathspec': False}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        value = arguments[index + 1] if index + 1 < len(arguments) else None
        if argument == '--':
            commit['pathspec'] = index + 1 < len(arguments)
            break
        if argument.startswith('--'):
            name, has_value, inline_value = argument.partition('=')
            if name in _REUSE_MESSAGE_OPTIONS:
                return None
            if name == '--message':
                messages.append(inline_value if has_value else value)
                index += 1 if has_value else 2
                continue
            if name == '--file':
                message_file = inline_value if has_value else value
                index += 1 if has_value else 2
                continue
            if name == '--all':
                commit['all'] = True
            elif name == '--amend':
                commit['amend'] = True
            elif name in ('--include', '--only'):
                commit['pathspec'] = True
            elif name in _COMMIT_OPTIONS_WITH_VALUE and not has_value:
                index += 1
            index += 1
            continue
        if argument.startswith('-') and len(argument) > 1:
            # `-am "..."` のようにまとめた短いオプションを1文字ずつ読む。値を取るものは残りか次の語を値とする。
            for position, flag in enumerate(argument[1:], 1):
                rest = argument[position + 1:]
                if flag in 'Cc':
                    return None
                # 値を続けて書くオプション（`-S<鍵>`・`-u<モード>`）の残りは、オプションとして読まない。
                if flag in 'Su':
                    break
                if flag in 'mF':
                    if rest:
                        flag_value = rest
                    else:
                        flag_value = value
                        index += 1
                    if flag == 'm':
                        messages.append(flag_value)
                    else:
                        message_file = flag_value
                    break
                if flag == 'a':
                    commit['all'] = True
                elif flag in 'io':
                    commit['pathspec'] = True
            index += 1
            continue
        commit['pathspec'] = True
        index += 1

    if any(message is None for message in messages):
        return None
    if messages:
        if heredoc is not None and len(messages) == 1 and _COMMAND_SUBSTITUTION_PATTERN.match(messages[0]):
            messages = [heredoc]
        commit['message'] = '\n\n'.join(messages)
    elif message_file == '-':
        if heredoc is None:
            return None
        commit['message'] = heredoc
    elif message_file is not None:
        try:
            with open(os.path.join(cwd, os.path.expanduser(message_file)), encoding='utf-8-sig') as file:
                commit['message'] = file.read()
        except (OSError, UnicodeError):
            return None
    else:
        return None
    return commit


def _find_commits(command, cwd):
    # コマンドに含まれるcommitごとに (commit, gitを実行するディレクトリ, 前に変更を載せる操作があったか) を返す。
    command, heredoc = _split_heredoc(command)
    try:
        segments = _split_segments(command)
    except ValueError:
        return []
    commits = []
    staged_before = False
    for segment in segments:
        if segment[0] == 'cd' and len(segment) == 2:
            cwd = os.path.join(cwd, os.path.expanduser(segment[1]))
            continue
        git = _parse_git(segment, cwd)
        if git is None:
            continue
        subcommand, arguments, git_cwd = git
        if subcommand in _STAGING_SUBCOMMANDS:
            staged_before = True
        elif subcommand == 'commit':
            commit = _parse_commit(arguments, git_cwd, heredoc)
            if commit is not None and commit['message'].strip():
                commits.append((commit, git_cwd, staged_before))
    return commits


def _run_git(cwd, *arguments):
    # gitの標準出力を返す。失敗した場合と、期限を過ぎた場合はNone。1回の上限は設けず、期限までの残り時間で打ち切る。
    return _git['get_git_output'](
        cwd, ['-c', 'core.quotePath=false', *arguments], float('inf'), _GIT_DEADLINE,
    )


def _get_commit_changes(commit, cwd, staged_before):
    # コミットに入る変更の (パスのリスト, 差分の本文) を返す。gitのリポジトリでなければNone。
    # 同じコマンドで先に変更を載せる場合や `-a`・パス指定では、作業ツリーの変更も含めて広めに見る。
    # 未追跡のファイルは、同じコマンドで先に変更を載せる場合（`git add -A` など）だけ含める。
    if not os.path.isdir(cwd):
        return None
    base_revision = 'HEAD~1' if commit['amend'] else 'HEAD'
    base = base_revision if _run_git(cwd, 'rev-parse', '--verify', '--quiet', base_revision) is not None else _EMPTY_TREE
    diff_options = ('--no-color', '--no-ext-diff')
    names = _run_git(cwd, 'diff', '--cached', '--name-only', *diff_options, base)
    patch = _run_git(cwd, 'diff', '--cached', *diff_options, base)
    if names is None or patch is None:
        return None
    paths = names.splitlines()
    texts = [patch]
    if staged_before or commit['all'] or commit['pathspec']:
        worktree_names = _run_git(cwd, 'diff', '--name-only', *diff_options, base) or ''
        untracked = (_run_git(cwd, 'ls-files', '--others', '--exclude-standard') or '') if staged_before else ''
        paths.extend(worktree_names.splitlines())
        paths.extend(untracked.splitlines())
        texts.append(_run_git(cwd, 'diff', *diff_options, base) or '')
        for path in untracked.splitlines():
            texts.append(path)
            try:
                with open(os.path.join(cwd, path), 'rb') as file:
                    texts.append(file.read(_MAX_UNTRACKED_FILE_BYTES).decode('utf-8', errors='replace'))
            except OSError:
                pass
    return sorted(set(path for path in paths if path)), '\n'.join(texts)


def _get_mentioned_tokens(message):
    # メッセージが名前を挙げたファイル・識別子を、出現順・重複なしで返す。
    tokens = []
    for span in _INLINE_CODE_PATTERN.findall(message):
        # 呼び出しの形で書いた関数名（`chr()` など）は、関数名で照合する。
        span = span.strip().removesuffix('()')
        # 空白を含むもの（コマンドや文）、プレースホルダーやglob（`<name>.py`・`*.md` など）、
        # 英字を含まないもの（`(#123)` など）は名前とみなさない。
        if (len(span) >= 3 and not any(character.isspace() or character in '<>*?[]{}' for character in span)
                and re.search(r'[^\W\d_]', span)):
            tokens.append(span)
    text = _URL_PATTERN.sub(' ', _INLINE_CODE_PATTERN.sub(' ', message))
    tokens.extend(match.group(0) for match in _BARE_FILE_PATTERN.finditer(text))
    return list(dict.fromkeys(tokens))


def _get_mismatches(message, paths, text):
    # 戻り値は (差分に現れない名前のリスト, テストの追加を書いたのにテストのファイルがないか)。
    haystack = '\n'.join([*paths, text]).casefold()
    missing = [token for token in _get_mentioned_tokens(message) if token.casefold() not in haystack]
    claims_tests = any(pattern.search(message) for pattern in _TEST_ADDITION_PATTERNS)
    missing_tests = claims_tests and not any(_TEST_PATH_PATTERN.search(path) for path in paths)
    return missing, missing_tests


def _record_denial(state_path, message):
    # 戻り値は、このメッセージを今回初めて拒否するか。同じメッセージの拒否はセッションで1回だけにし、意図したメッセージなら再実行で通す。
    # 状態にはメッセージを保存せず、照合用のSHA-256だけを保存する。
    return bool(_filter_first_time_denials(state_path, [(_get_text_sha256_digest(message), message)]))


def _get_deny_reason(missing, missing_tests):
    lines = [f'{_DENY_REASON_PREFIX}.']
    if missing:
        lines.append('These names in the message appear in neither the changed paths nor the diff:')
        lines.extend(f'- {token}' for token in missing[:_MAX_LISTED_TOKENS])
        if len(missing) > _MAX_LISTED_TOKENS:
            lines.append(f'- ... and {len(missing) - _MAX_LISTED_TOKENS} more')
    if missing_tests:
        lines.append('The message says tests were added, but no changed path looks like a test file.')
    lines.append(
        'Check the staged diff (`git diff --cached`). Stage the missing changes, or rewrite the message so that it '
        'describes only what this commit contains. If the message is already accurate, run the same command again; '
        'this check denies each message only once per session.'
    )
    return '\n'.join(lines)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    # 拒否理由は複数行のため、Windowsでも `\n` を `\r\n` に変えずに出力する。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('block-commit-message-mismatch', hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        tool_args = _unwrap_tool_args(tool_args) if _tool_kinds['is_shell_tool'](tool_name) else None
        command = tool_args.get('command') if isinstance(tool_args, dict) else None

        reason = None
        for commit, git_cwd, staged_before in _find_commits(command, cwd) if isinstance(command, str) else []:
            changes = _get_commit_changes(commit, git_cwd, staged_before)
            # gitのリポジトリでない場合や、コミットする変更がない場合はgit自身の結果に任せる。
            if changes is None or not changes[0]:
                continue
            missing, missing_tests = _get_mismatches(commit['message'], *changes)
            if not missing and not missing_tests:
                continue
            if _record_denial(state_path, commit['message']):
                reason = _get_deny_reason(missing, missing_tests)
                break
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check the commit message: {ex}', file=sys.stderr)
        return 2

    if reason is not None:
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
