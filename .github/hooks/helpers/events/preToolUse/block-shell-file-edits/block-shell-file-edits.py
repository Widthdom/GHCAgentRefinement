import glob
import io
import json
import os
import re
import runpy
import shlex
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))


_GIT_TIMEOUT_SECONDS = 5
_SHELL_TOOL_PATTERN = re.compile(r'^(?:functions\.)?(bash|powershell)$', re.IGNORECASE)
_DENY_REASON = (
    'Shell command denied: it writes to the following git-tracked files: {files}. '
    'Writing files through the shell bypasses the hooks for file edit tools '
    '(read-before-edit check, line-ending preservation, skill wrapper check). '
    'Make the change with the file edit tools (edit, create, apply_patch, etc.) instead, '
    'following the edit-files-with-policy skill. Do not stop the task because of this denial. '
    'If an edit tool cannot make this change (for example, a binary file), explain why to the user and ask how to proceed.'
)

# bashのリダイレクトのうち、ファイルへ書き込むもの。`>&` は複製先が数字や `-` でなければ書き込みとみなす。
_BASH_WRITE_OPERATORS = {'>', '>>', '>|', '&>', '&>>', '<>'}
# 直後の語を読み込み元やヒアドキュメントの区切り語として読み飛ばすもの。
_BASH_SKIP_OPERATORS = {'<', '<<', '<<-', '<<<', '<&', '>&'}
_BASH_PUNCTUATION = set('();<>|&')
_BASH_WRAPPERS = {'builtin', 'command', 'env', 'exec', 'nohup', 'sudo', 'time', 'xargs'}
_BASH_CD_COMMANDS = {'cd', 'pushd'}
# `<<<` は文字列を渡すだけなので、ヒアドキュメントとみなさない。
_HEREDOC_PATTERN = re.compile(
    r'(?<!<)<<(?!<)(-?)\s*(?:\'([^\'\n]*)\'|"([^"\n]*)"|\\?([^\s;&|()<>\'"]+))'
)
# sedは `-i`・`-i.bak`・`-ni`・`--in-place[=SUFFIX]`、perlは `-i`・`-pi`・`-i.bak` で上書き編集する。
_SED_IN_PLACE_PATTERN = re.compile(r'^(?:-[A-Za-z]*i|--in-place(?:=|$))')
_PERL_IN_PLACE_PATTERN = re.compile(r'^-[nplaswTtUWX0-9]*i')

_POWERSHELL_WRITE_COMMANDS = {
    'ac', 'add-content', 'clc', 'clear-content', 'out-file', 'sc', 'set-content', 'tee', 'tee-object',
}
_POWERSHELL_CD_COMMANDS = {'cd', 'chdir', 'push-location', 'pushd', 'set-location', 'sl'}
# `2>&1` などの複製は書き込みではない。
_POWERSHELL_REDIRECT_PATTERN = re.compile(r'[1-6*]?>>?(&[1-6])?')
_POWERSHELL_SEPARATORS = set(';|(){}&\n')
_POWERSHELL_FILE_API_PATTERN = re.compile(
    r'\[(?:System\.)?IO\.File\]::(?:WriteAll(?:Text|Lines|Bytes)|AppendAll(?:Text|Lines))\s*\(\s*'
    r'(?:\'((?:[^\']|\'\')*)\'|"([^"`$]*)")',
    re.IGNORECASE,
)


def _is_bash_punctuation(token):
    return bool(token) and all(character in _BASH_PUNCTUATION for character in token)


def _split_bash(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _get_bash_lines(command):
    # 引用符が閉じるまでと行末の `\` は次の行とつなげ、論理行ごとに字句へ分ける。
    # ヒアドキュメントの本文は書き込み先ではないため読み飛ばす。
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


def _tokenize_bash(command):
    # 戻り値は ('word', 語)・('write', 書き込み先)・('separator', 区切り) の列。
    items = []
    for tokens in _get_bash_lines(command):
        index = 0
        while index < len(tokens):
            token = tokens[index]
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if token.isdigit() and (following in _BASH_WRITE_OPERATORS or following in _BASH_SKIP_OPERATORS):
                # `2>` のファイル記述子は引数ではない。
                index += 1
                continue
            is_write = token in _BASH_WRITE_OPERATORS or (
                token == '>&' and following is not None and not re.fullmatch(r'\d+|-', following)
            )
            if is_write or token in _BASH_SKIP_OPERATORS:
                if following is not None and not _is_bash_punctuation(following):
                    if is_write:
                        items.append(('write', following))
                    index += 2
                    continue
            if _is_bash_punctuation(token):
                items.append(('separator', token))
            else:
                items.append(('word', token))
            index += 1
        items.append(('separator', '\n'))
    return items


def _read_powershell_quoted(command, index):
    # 戻り値は (引用符を外した文字列, 閉じ引用符の次の位置)。
    quote = command[index]
    index += 1
    text = []
    while index < len(command):
        character = command[index]
        if character == quote:
            if command.startswith(quote * 2, index):
                text.append(quote)
                index += 2
                continue
            return ''.join(text), index + 1
        if quote == '"' and character == '`' and index + 1 < len(command):
            text.append(command[index + 1])
            index += 2
            continue
        text.append(character)
        index += 1
    return ''.join(text), index


def _tokenize_powershell(command):
    # 戻り値はtokenize_bashと同じ形式。
    items = []
    index = 0
    pending_write = False
    word = None

    def finish_word():
        nonlocal word, pending_write
        if word is not None:
            items.append(('write' if pending_write else 'word', word))
            pending_write = False
            word = None

    while index < len(command):
        character = command[index]
        if character in ' \t\r\f':
            finish_word()
            index += 1
            continue
        if character == '`':
            # 行末のバッククォートは行の継続、それ以外は次の1文字をそのまま扱う。
            if command.startswith('\n', index + 1) or command.startswith('\r\n', index + 1):
                finish_word()
                index += 3 if command[index + 1] == '\r' else 2
                continue
            word = (word or '') + command[index + 1:index + 2]
            index += 2
            continue
        if word is None:
            if command.startswith('<#', index):
                end = command.find('#>', index + 2)
                index = len(command) if end < 0 else end + 2
                continue
            if character == '#':
                end = command.find('\n', index)
                index = len(command) if end < 0 else end
                continue
            here_string = re.match(r'@([\'"])[ \t]*\r?\n', command[index:])
            if here_string:
                quote = here_string.group(1)
                end = re.compile(r'\r?\n' + quote + '@').search(command, index + here_string.end())
                body_end = len(command) if end is None else end.start()
                items.append(('write' if pending_write else 'word', command[index + here_string.end():body_end]))
                pending_write = False
                index = len(command) if end is None else end.end()
                continue
            redirect = _POWERSHELL_REDIRECT_PATTERN.match(command, index)
            if redirect and redirect.group(0).lstrip('123456*').startswith('>'):
                # 複製でなければ、次の語を書き込み先とする。
                pending_write = redirect.group(1) is None
                index = redirect.end()
                continue
        if character in _POWERSHELL_SEPARATORS:
            finish_word()
            pending_write = False
            items.append(('separator', character))
            index += 1
            continue
        if character == '>':
            finish_word()
            continue
        if character in '\'"':
            text, index = _read_powershell_quoted(command, index)
            word = (word or '') + text
            continue
        word = (word or '') + character
        index += 1
    finish_word()
    items.append(('separator', '\n'))
    return items


def _get_command_name(word, shell):
    name = re.split(r'[\\/]', word)[-1]
    if shell == 'powershell' or name.lower().endswith('.exe'):
        name = name.lower().removesuffix('.exe')
    return name


def _split_command(words, shell):
    # 戻り値は (コマンド名, 引数のリスト)。bashでは変数代入と、sudoなどの前置きを読み飛ばす。
    index = 0
    if shell == 'bash':
        after_wrapper = False
        while index < len(words):
            word = words[index]
            if re.match(r'^[A-Za-z_]\w*=', word) or (after_wrapper and word.startswith('-')):
                index += 1
                continue
            if _get_command_name(word, shell) in _BASH_WRAPPERS:
                after_wrapper = True
                index += 1
                continue
            break
    if index >= len(words):
        return None, []
    return _get_command_name(words[index], shell), words[index + 1:]


def _get_powershell_operands(args):
    # パラメーター名を除いた値。`-Path:value` の値も含め、配列の `,` は分ける。
    operands = []
    for arg in args:
        if re.match(r'^-[A-Za-z]', arg):
            if ':' not in arg:
                continue
            arg = arg.split(':', 1)[1]
        operands.extend(value for value in arg.split(',') if value)
    return operands


def _get_written_operands(name, args, shell):
    # 書き込み先を絞り込まず、オプション以外の語をすべて候補とする。git管理ファイルとの照合で絞られる。
    if shell == 'powershell' and name in _POWERSHELL_WRITE_COMMANDS:
        return _get_powershell_operands(args)
    operands = [arg for arg in args if not arg.startswith('-')]
    if name == 'sed' and any(_SED_IN_PLACE_PATTERN.match(arg) for arg in args):
        return operands
    if name == 'perl' and any(_PERL_IN_PLACE_PATTERN.match(arg) for arg in args):
        return operands
    if shell == 'bash' and name == 'tee':
        return operands
    return []


def _get_changed_directory(name, args, shell, current):
    # 移動先が1つの文字列で示された場合だけ、後続のコマンドの基準を移す。
    if shell == 'powershell' and name in _POWERSHELL_CD_COMMANDS:
        operands = _get_powershell_operands(args)
    elif shell == 'bash' and name in _BASH_CD_COMMANDS:
        operands = [arg for arg in args if not arg.startswith('-')]
    else:
        return current
    if len(operands) != 1 or '$' in operands[0]:
        return current
    return os.path.normpath(os.path.join(current, os.path.expanduser(operands[0])))


def _get_write_targets(command, shell, cwd):
    # 戻り値は (基準ディレクトリ, 書き込み先の表記) のリスト。
    items = _tokenize_bash(command) if shell == 'bash' else _tokenize_powershell(command)
    targets = []
    current = cwd
    words = []
    for kind, text in items:
        if kind == 'write':
            targets.append((current, text))
        elif kind == 'word':
            words.append(text)
        else:
            name, args = _split_command(words, shell)
            if name is not None:
                targets.extend((current, operand) for operand in _get_written_operands(name, args, shell))
                current = _get_changed_directory(name, args, shell, current)
            words = []
    if shell == 'powershell':
        for match in _POWERSHELL_FILE_API_PATTERN.finditer(command):
            single_quoted, double_quoted = match.groups()
            targets.append((cwd, single_quoted.replace("''", "'") if single_quoted is not None else double_quoted))
    return targets


def _resolve_paths(base, text):
    # 変数やコマンド置換を含む表記は、展開後の値がわからないため対象外とする。
    if not text.strip() or '$' in text or '`' in text:
        return []
    path = os.path.join(base, os.path.expanduser(text))
    if re.search(r'[*?[]', text):
        matches = glob.glob(path)
        if matches:
            return [os.path.normpath(match) for match in matches]
    return [os.path.normpath(path)]


def _get_tracked_files(paths):
    # 既存のファイルを親ディレクトリごとにまとめ、gitの管理対象かを調べる。
    # 大文字小文字を区別しないファイルシステムでも照合できるよう、一覧をnormcaseで比べる。
    by_directory = {}
    for path in paths:
        if os.path.isfile(path):
            by_directory.setdefault(os.path.dirname(path), set()).add(path)
    tracked = set()
    for directory, files in by_directory.items():
        output = _git['get_git_output'](directory, ['ls-files', '-z'], _GIT_TIMEOUT_SECONDS)
        # gitがない場合は判定できないため許可する。gitの導入はcheck-required-tools hookが案内する。
        if output is None:
            continue
        names = {os.path.normcase(name) for name in output.split('\0') if name and '/' not in name}
        tracked.update(path for path in files if os.path.normcase(os.path.basename(path)) in names)
    return tracked


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()

        cwd = _hook_input['get_cwd'](hook_input)
        cwd = os.path.normpath(cwd)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        paths = set()
        match = _SHELL_TOOL_PATTERN.match(tool_name)
        tool_args = _unwrap_tool_args(tool_args) if match else None
        command = tool_args.get('command') if isinstance(tool_args, dict) else None
        if match and isinstance(command, str):
            for base, text in _get_write_targets(command, match.group(1).lower(), cwd):
                paths.update(_resolve_paths(base, text))
        tracked = _get_tracked_files(paths)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check shell file edits: {ex}', file=sys.stderr)
        return 2

    if tracked:
        # 大文字小文字だけが異なる表記は、同じファイルとして1つにまとめる。
        display_paths = {os.path.normcase(path): _get_display_path(path, cwd) for path in sorted(tracked)}
        files = ', '.join(sorted(display_paths.values()))
        # SDK形式のトップレベルpermissionDecision。正常な拒否なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({
            'permissionDecision': 'deny',
            'permissionDecisionReason': _DENY_REASON.format(files=files),
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
