import glob
import io
import json
import os
import re
import shlex
import subprocess
import sys
from typing import cast


SHELL_TOOL_PATTERN = re.compile(r'^(?:functions\.)?(bash|powershell)$', re.IGNORECASE)
DENY_REASON = (
    'Shell command denied: it writes to the following git-tracked files: {files}. '
    'Writing files through the shell bypasses the hooks for file edit tools '
    '(read-before-edit check, line-ending preservation, skill wrapper check). '
    'Make the change with the file edit tools (edit, create, apply_patch, etc.) instead, '
    'following the edit-files-with-policy skill. Do not stop the task because of this denial. '
    'If an edit tool cannot make this change (for example, a binary file), explain why to the user and ask how to proceed.'
)

# bashのリダイレクトのうち、ファイルへ書き込むもの。`>&` は複製先が数字や `-` でなければ書き込みとみなす。
BASH_WRITE_OPERATORS = {'>', '>>', '>|', '&>', '&>>', '<>'}
# 直後の語を読み込み元やヒアドキュメントの区切り語として読み飛ばすもの。
BASH_SKIP_OPERATORS = {'<', '<<', '<<-', '<<<', '<&', '>&'}
BASH_PUNCTUATION = set('();<>|&')
BASH_WRAPPERS = {'builtin', 'command', 'env', 'exec', 'nohup', 'sudo', 'time', 'xargs'}
BASH_CD_COMMANDS = {'cd', 'pushd'}
# `<<<` は文字列を渡すだけなので、ヒアドキュメントとみなさない。
HEREDOC_PATTERN = re.compile(
    r'(?<!<)<<(?!<)(-?)\s*(?:\'([^\'\n]*)\'|"([^"\n]*)"|\\?([^\s;&|()<>\'"]+))'
)
# sedは `-i`・`-i.bak`・`-ni`・`--in-place[=SUFFIX]`、perlは `-i`・`-pi`・`-i.bak` で上書き編集する。
SED_IN_PLACE_PATTERN = re.compile(r'^(?:-[A-Za-z]*i|--in-place(?:=|$))')
PERL_IN_PLACE_PATTERN = re.compile(r'^-[nplaswTtUWX0-9]*i')

POWERSHELL_WRITE_COMMANDS = {
    'ac', 'add-content', 'clc', 'clear-content', 'out-file', 'sc', 'set-content', 'tee', 'tee-object',
}
POWERSHELL_CD_COMMANDS = {'cd', 'chdir', 'push-location', 'pushd', 'set-location', 'sl'}
# `2>&1` などの複製は書き込みではない。
POWERSHELL_REDIRECT_PATTERN = re.compile(r'[1-6*]?>>?(&[1-6])?')
POWERSHELL_SEPARATORS = set(';|(){}&\n')
POWERSHELL_FILE_API_PATTERN = re.compile(
    r'\[(?:System\.)?IO\.File\]::(?:WriteAll(?:Text|Lines|Bytes)|AppendAll(?:Text|Lines))\s*\(\s*'
    r'(?:\'((?:[^\']|\'\')*)\'|"([^"`$]*)")',
    re.IGNORECASE,
)


def is_bash_punctuation(token):
    return bool(token) and all(character in BASH_PUNCTUATION for character in token)


def split_bash(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def get_bash_lines(command):
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
            tokens = split_bash(buffer)
        except ValueError:
            buffer += '\n'
            continue
        yield tokens
        for match in HEREDOC_PATTERN.finditer(buffer):
            heredocs.append((next(group for group in match.groups()[1:] if group is not None), match.group(1) == '-'))
        buffer = ''
    # 引用符が閉じないまま終わった部分は判定できないため対象外とする。


def tokenize_bash(command):
    # 戻り値は ('word', 語)・('write', 書き込み先)・('separator', 区切り) の列。
    items = []
    for tokens in get_bash_lines(command):
        index = 0
        while index < len(tokens):
            token = tokens[index]
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if token.isdigit() and (following in BASH_WRITE_OPERATORS or following in BASH_SKIP_OPERATORS):
                # `2>` のファイル記述子は引数ではない。
                index += 1
                continue
            is_write = token in BASH_WRITE_OPERATORS or (
                token == '>&' and following is not None and not re.fullmatch(r'\d+|-', following)
            )
            if is_write or token in BASH_SKIP_OPERATORS:
                if following is not None and not is_bash_punctuation(following):
                    if is_write:
                        items.append(('write', following))
                    index += 2
                    continue
            if is_bash_punctuation(token):
                items.append(('separator', token))
            else:
                items.append(('word', token))
            index += 1
        items.append(('separator', '\n'))
    return items


def read_powershell_quoted(command, index):
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


def tokenize_powershell(command):
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
            redirect = POWERSHELL_REDIRECT_PATTERN.match(command, index)
            if redirect and redirect.group(0).lstrip('123456*').startswith('>'):
                # 複製でなければ、次の語を書き込み先とする。
                pending_write = redirect.group(1) is None
                index = redirect.end()
                continue
        if character in POWERSHELL_SEPARATORS:
            finish_word()
            pending_write = False
            items.append(('separator', character))
            index += 1
            continue
        if character == '>':
            finish_word()
            continue
        if character in '\'"':
            text, index = read_powershell_quoted(command, index)
            word = (word or '') + text
            continue
        word = (word or '') + character
        index += 1
    finish_word()
    items.append(('separator', '\n'))
    return items


def get_command_name(word, shell):
    name = re.split(r'[\\/]', word)[-1]
    if shell == 'powershell' or name.lower().endswith('.exe'):
        name = name.lower().removesuffix('.exe')
    return name


def split_command(words, shell):
    # 戻り値は (コマンド名, 引数のリスト)。bashでは変数代入と、sudoなどの前置きを読み飛ばす。
    index = 0
    if shell == 'bash':
        after_wrapper = False
        while index < len(words):
            word = words[index]
            if re.match(r'^[A-Za-z_]\w*=', word) or (after_wrapper and word.startswith('-')):
                index += 1
                continue
            if get_command_name(word, shell) in BASH_WRAPPERS:
                after_wrapper = True
                index += 1
                continue
            break
    if index >= len(words):
        return None, []
    return get_command_name(words[index], shell), words[index + 1:]


def get_powershell_operands(args):
    # パラメーター名を除いた値。`-Path:value` の値も含め、配列の `,` は分ける。
    operands = []
    for arg in args:
        if re.match(r'^-[A-Za-z]', arg):
            if ':' not in arg:
                continue
            arg = arg.split(':', 1)[1]
        operands.extend(value for value in arg.split(',') if value)
    return operands


def get_written_operands(name, args, shell):
    # 書き込み先を絞り込まず、オプション以外の語をすべて候補とする。git管理ファイルとの照合で絞られる。
    if shell == 'powershell' and name in POWERSHELL_WRITE_COMMANDS:
        return get_powershell_operands(args)
    operands = [arg for arg in args if not arg.startswith('-')]
    if name == 'sed' and any(SED_IN_PLACE_PATTERN.match(arg) for arg in args):
        return operands
    if name == 'perl' and any(PERL_IN_PLACE_PATTERN.match(arg) for arg in args):
        return operands
    if shell == 'bash' and name == 'tee':
        return operands
    return []


def get_changed_directory(name, args, shell, current):
    # 移動先が1つの文字列で示された場合だけ、後続のコマンドの基準を移す。
    if shell == 'powershell' and name in POWERSHELL_CD_COMMANDS:
        operands = get_powershell_operands(args)
    elif shell == 'bash' and name in BASH_CD_COMMANDS:
        operands = [arg for arg in args if not arg.startswith('-')]
    else:
        return current
    if len(operands) != 1 or '$' in operands[0]:
        return current
    return os.path.normpath(os.path.join(current, os.path.expanduser(operands[0])))


def get_write_targets(command, shell, cwd):
    # 戻り値は (基準ディレクトリ, 書き込み先の表記) のリスト。
    items = tokenize_bash(command) if shell == 'bash' else tokenize_powershell(command)
    targets = []
    current = cwd
    words = []
    for kind, text in items:
        if kind == 'write':
            targets.append((current, text))
        elif kind == 'word':
            words.append(text)
        else:
            name, args = split_command(words, shell)
            if name is not None:
                targets.extend((current, operand) for operand in get_written_operands(name, args, shell))
                current = get_changed_directory(name, args, shell, current)
            words = []
    if shell == 'powershell':
        for match in POWERSHELL_FILE_API_PATTERN.finditer(command):
            single_quoted, double_quoted = match.groups()
            targets.append((cwd, single_quoted.replace("''", "'") if single_quoted is not None else double_quoted))
    return targets


def resolve_paths(base, text):
    # 変数やコマンド置換を含む表記は、展開後の値がわからないため対象外とする。
    if not text.strip() or '$' in text or '`' in text:
        return []
    path = os.path.join(base, os.path.expanduser(text))
    if re.search(r'[*?[]', text):
        matches = glob.glob(path)
        if matches:
            return [os.path.normpath(match) for match in matches]
    return [os.path.normpath(path)]


def get_tracked_files(paths):
    # 既存のファイルを親ディレクトリごとにまとめ、gitの管理対象かを調べる。
    # 大文字小文字を区別しないファイルシステムでも照合できるよう、一覧をnormcaseで比べる。
    by_directory = {}
    for path in paths:
        if os.path.isfile(path):
            by_directory.setdefault(os.path.dirname(path), set()).add(path)
    tracked = set()
    for directory, files in by_directory.items():
        try:
            result = subprocess.run(
                ['git', '-C', directory, 'ls-files', '-z'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            # gitがない場合は判定できないため許可する。gitの導入はcheck-git hookが案内する。
            continue
        if result.returncode != 0:
            continue
        names = {
            os.path.normcase(name) for name in result.stdout.decode('utf-8', errors='replace').split('\0')
            if name and '/' not in name
        }
        tracked.update(path for path in files if os.path.normcase(os.path.basename(path)) in names)
    return tracked


def get_display_path(path, cwd):
    # cwd配下のファイルは相対パスで示し、理由を短くする。大文字小文字は表記のまま示す。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.normcase(os.path.commonpath([cwd, path])) == os.path.normcase(cwd):
            return os.path.relpath(path, cwd)
    except ValueError:
        pass
    return path


def get_tool_args(raw_tool_args):
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
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')

        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd must be a non-empty string.')
        if not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        cwd = os.path.normpath(cwd)
        # preToolUseには、並列に呼び出すツールがまとめて渡される。
        tool_calls = hook_input.get('toolCalls')
        if not isinstance(tool_calls, list) or not tool_calls:
            raise ValueError('toolCalls must be a non-empty array.')

        paths = set()
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                raise ValueError('each tool call must be a JSON object.')
            tool_name = tool_call.get('name')
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError('each tool call requires a non-empty name.')
            match = SHELL_TOOL_PATTERN.match(tool_name)
            if not match:
                continue
            tool_args = get_tool_args(tool_call.get('args'))
            command = tool_args.get('command') if isinstance(tool_args, dict) else None
            if not isinstance(command, str):
                continue
            for base, text in get_write_targets(command, match.group(1).lower(), cwd):
                paths.update(resolve_paths(base, text))
        tracked = get_tracked_files(paths)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check shell file edits: {ex}', file=sys.stderr)
        return 2

    if tracked:
        # 大文字小文字だけが異なる表記は、同じファイルとして1つにまとめる。
        display_paths = {os.path.normcase(path): get_display_path(path, cwd) for path in sorted(tracked)}
        files = ', '.join(sorted(display_paths.values()))
        # SDK形式のトップレベルpermissionDecision。正常な拒否なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({
            'permissionDecision': 'deny',
            'permissionDecisionReason': DENY_REASON.format(files=files),
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
