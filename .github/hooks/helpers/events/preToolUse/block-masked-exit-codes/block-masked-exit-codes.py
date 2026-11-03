import hashlib
import io
import json
import os
import re
import runpy
import shlex
import sqlite3
import sys
import tempfile
import uuid
from contextlib import closing
from typing import cast


# powershellはパイプラインの終了コードの扱いが異なるため、bashだけを対象にする。
_SHELL_TOOL_PATTERN = re.compile(r'^(?:functions\.)?bash$', re.IGNORECASE)
# 終了コードが成否を表すコマンドのうち、テスト以外（ビルド・静的検査）の語。
# テストの語は、check-test-runs utilityがテストの実行とみなす語を使う。
_BUILD_AND_LINT_COMMAND_WORDS = ('build', 'msbuild', 'make', 'tsc', 'lint', 'eslint', 'ruff', 'mypy', 'clippy')
# 出力やファイルを読むだけのコマンド。`grep test` や `ls build` をテスト・ビルドとみなさない。
_READER_COMMANDS = {
    'ag', 'awk', 'cat', 'cut', 'diff', 'du', 'echo', 'egrep', 'fd', 'fgrep', 'file', 'find', 'git', 'grep',
    'head', 'jq', 'less', 'ls', 'more', 'printf', 'rg', 'sed', 'sort', 'stat', 'tail', 'tr', 'tree', 'uniq',
    'wc', 'which', 'yq',
}
# 後続の語が実際のコマンドになるもの。`timeout` は次の時間指定も読み飛ばす。
_WRAPPER_COMMANDS = {'builtin', 'command', 'env', 'exec', 'nice', 'nohup', 'sudo', 'time', 'xargs'}
_ASSIGNMENT_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_PIPE_OPERATORS = {'|', '|&'}
_REDIRECT_OPERATORS = {'>', '>>', '>|', '&>', '&>>', '<', '<<', '<<-', '<<<', '<&', '>&', '<>'}
# `<<<` は文字列を渡すだけなので、ヒアドキュメントとみなさない。
_HEREDOC_PATTERN = re.compile(
    r'(?<!<)<<(?!<)(-?)\s*(?:\'([^\'\n]*)\'|"([^"\n]*)"|\\?([^\s;&|()<>\'"]+))'
)
# `set -o pipefail`・`set -euo pipefail` などの `o` を含むオプションの直後の語がpipefailなら設定とみなす。
_SET_OPTION_PATTERN = re.compile(r'^([-+])[A-Za-z]*o$')
_MAX_COMMAND_LENGTH = 120
_DENY_REASON = (
    'Shell command denied: a pipe hides the exit status of {commands}. '
    'Without pipefail, a pipeline exits with the status of its last command (tail, grep, tee, etc.), '
    'so a failing test or build is reported as a success, and the result cannot be used as evidence. '
    'Prepend "set -o pipefail; " to the command, or write the output to a file and inspect it in a separate step. '
    'If the exit status really does not matter here, run the same command again; '
    'this check denies the same command only once per session.'
)


def _split_bash(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _get_bash_lines(command):
    # 引用符が閉じるまでと行末の `\` は次の行とつなげ、論理行ごとに字句へ分ける。
    # ヒアドキュメントの本文はコマンドではないため読み飛ばす。
    heredocs = []
    buffer = ''
    for line in command.replace('\r\n', '\n').split('\n'):
        if heredocs:
            delimiter, strip_tabs = heredocs[0]
            if (line.lstrip('\t') if strip_tabs else line) == delimiter:
                heredocs.pop(0)
            continue
        buffer += line
        if re.search(r'(?<!\\)(?:\\\\)*\\$', buffer):
            buffer = buffer[:-1]
            continue
        try:
            tokens = _split_bash(buffer)
        except ValueError:
            buffer += '\n'
            continue
        yield tokens
        for match in _HEREDOC_PATTERN.finditer(buffer):
            heredocs.append((next(group for group in match.groups()[1:] if group is not None), match.group(1) == '-'))
        buffer = ''
    # 引用符が閉じないまま終わった部分は判定できないため対象外とする。


def _get_pipelines(command):
    # 戻り値は (パイプラインの各段の語のリストのリスト, 直前までにpipefailが有効か) の列。
    pipefail = False
    for tokens in _get_bash_lines(command):
        segments = [[]]
        index = 0
        while index <= len(tokens):
            token = tokens[index] if index < len(tokens) else ';'
            index += 1
            if token in _REDIRECT_OPERATORS:
                # `2>&1` の記述子、リダイレクト先、ヒアドキュメントの区切り語はコマンドの語ではない。
                if segments[-1] and segments[-1][-1].isdigit():
                    segments[-1].pop()
                index += 1
                continue
            if token in _PIPE_OPERATORS:
                segments.append([])
                continue
            if all(character in '();&|<>' for character in token):
                # `;`・`&&`・`||`・`&`・括弧と改行で、パイプラインが終わる。
                segments = [segment for segment in segments if segment]
                if segments:
                    yield segments, pipefail
                    if len(segments) == 1:
                        pipefail = _get_pipefail(segments[0], pipefail)
                segments = [[]]
                continue
            segments[-1].append(token)


def _get_pipefail(words, current):
    # `set -o pipefail` で有効、`set +o pipefail` で無効にする。
    if words[0] != 'set':
        return current
    for option, following in zip(words[1:], words[2:]):
        match = _SET_OPTION_PATTERN.match(option)
        if match and following == 'pipefail':
            current = match.group(1) == '-'
    return current


def _get_command_words(words):
    # 環境変数の代入とラッパーを読み飛ばし、実際に実行するコマンド以降の語を返す。
    index = 0
    while index < len(words):
        word = words[index]
        if _ASSIGNMENT_PATTERN.match(word) or word.startswith('-'):
            index += 1
        elif word in _WRAPPER_COMMANDS:
            index += 1
        elif word == 'timeout':
            index += 2
        else:
            break
    return words[index:]


def _is_checked_command(words, checked_command_pattern):
    command_words = _get_command_words(words)
    if not command_words or os.path.basename(command_words[0]) in _READER_COMMANDS:
        return False
    return bool(checked_command_pattern.search(' '.join(command_words)))


def _get_masked_commands(command, checked_command_pattern):
    # 最後の段以外にテスト・ビルドがあり、pipefailが無効なパイプラインのテスト・ビルドを返す。
    # `PIPESTATUS` を参照している場合は、各段の終了コードを確かめているとみなす。
    if 'PIPESTATUS' in command:
        return []
    masked = []
    for segments, pipefail in _get_pipelines(command):
        if pipefail or len(segments) < 2:
            continue
        masked.extend(' '.join(segment) for segment in segments[:-1] if _is_checked_command(segment, checked_command_pattern))
    return masked


def _format_command(command):
    if len(command) > _MAX_COMMAND_LENGTH:
        command = command[:_MAX_COMMAND_LENGTH] + '...'
    return f'"{command}"'


def _record_denials(state_path, command_keys):
    # 戻り値は、今回初めて拒否するコマンドがあるか。同じコマンドの拒否は1回だけにし、意図した実行なら再実行で通す。
    # 前のツール呼び出しで `set -o pipefail` を済ませた場合など、hookから見えない事情で作業を止めないため。
    # SQLiteのトランザクションで、並列のツール呼び出しでも重複して拒否しない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    denied = False
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS denials (command TEXT PRIMARY KEY)')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for command_key in command_keys:
                if connection.execute('INSERT OR IGNORE INTO denials (command) VALUES (?)', (command_key,)).rowcount:
                    denied = True
    return denied


def _get_tool_args(raw_tool_args):
    # argsはJSONオブジェクトのほか、JSON文字列で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    try:
        # 終了コードが成否を表すコマンド（テスト・ビルド・静的検査）。`latest` などの語中は除く。
        check_test_runs = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'check-test-runs', 'check-test-runs.py',
        ))
        checked_command_pattern = check_test_runs['compile_command_pattern'](
            (*check_test_runs['TEST_COMMAND_WORDS'], *_BUILD_AND_LINT_COMMAND_WORDS)
        )
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        session_id = hook_input.get('sessionId')
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('sessionId must be a non-empty UUID string.')
        try:
            parsed_session_id = uuid.UUID(session_id.strip())
        except ValueError:
            raise ValueError('sessionId must be a valid UUID.') from None
        # preToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        masked = []
        command_keys = []
        tool_args = _get_tool_args(hook_input['toolArgs']) if _SHELL_TOOL_PATTERN.match(tool_name) else None
        command = tool_args.get('command') if isinstance(tool_args, dict) else None
        commands = _get_masked_commands(command, checked_command_pattern) if isinstance(command, str) else []
        if commands:
            masked.extend(commands)
            # 状態にはコマンドを保存せず、照合用のSHA-256だけを保存する。
            command_keys.append(hashlib.sha256(command.encode('utf-8')).hexdigest())

        denied = False
        if command_keys:
            # hookは呼び出しごとに別プロセスなので、拒否済みのコマンドをセッションUUID別に保存する。
            # DBファイル自体は自動削除しない。
            state_path = os.path.join(
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-masked-exit-codes',
                f'{parsed_session_id}.sqlite3',
            )
            denied = _record_denials(state_path, command_keys)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check masked exit codes: {ex}', file=sys.stderr)
        return 2

    if denied:
        commands = ', '.join(_format_command(command) for command in dict.fromkeys(masked))
        # SDK形式のトップレベルpermissionDecision。正常な拒否なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({
            'permissionDecision': 'deny',
            'permissionDecisionReason': _DENY_REASON.format(commands=commands),
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
