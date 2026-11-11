import os
import re
import runpy

# コマンドがテストの実行か、応答が「テストが通った」と書いているかを判定する。
# テストの実行結果を扱うhook（verify-test-pass-claims・require-hook-tests・record-test-failure-values・detect-vacuous-test-success）と、
# テストを含む検証コマンドの終了コードを隠させないhook（block-masked-exit-codes）が共有する。

_split_bash_lines = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'split-bash-lines', 'split-bash-lines.py',
))['split_bash_lines']

# テストの実行とみなすコマンドの語（正規表現）。`test-all-hooks.py`・`npm test`・`pytest` などを含む。
TEST_COMMAND_WORDS = ('tests?', 'pytest', 'unittest', 'jest', 'vitest', 'mocha', 'rspec', 'phpunit', 'ctest', 'tox', 'pester')
# 出力やファイルを読むだけのコマンドと、移動するだけのコマンド。`grep test`・`ls tests`・`cd tests` をテストの実行とみなさない。
# PowerShellのコマンドレットは大文字小文字を区別せずに照合するため、小文字で書く。
_READER_COMMANDS = {
    'ag', 'awk', 'cat', 'cd', 'cut', 'diff', 'du', 'echo', 'egrep', 'fd', 'fgrep', 'file', 'find', 'git', 'grep',
    'head', 'jq', 'less', 'ls', 'more', 'printf', 'pushd', 'rg', 'sed', 'sort', 'stat', 'tail', 'tr', 'tree', 'uniq',
    'wc', 'which', 'yq', 'get-childitem', 'get-content', 'select-string', 'set-location', 'write-host', 'write-output',
}
# 後続の語が実際のコマンドになるもの。`timeout` は次の時間指定も読み飛ばす。
_WRAPPER_COMMANDS = {'builtin', 'command', 'env', 'exec', 'nice', 'nohup', 'sudo', 'time', 'xargs'}
_ASSIGNMENT_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_REDIRECT_OPERATORS = {'>', '>>', '>|', '&>', '&>>', '<', '<<', '<<-', '<<<', '<&', '>&', '<>'}
_OPERATOR_CHARACTERS = frozenset('();&|<>')
# `set -e`・`set -euo pipefail`・`set -o errexit` があれば、後続のコマンドの失敗で終了する。
_ERREXIT_PATTERN = re.compile(r'(?:^|[\s;&|(])set\s+(?:-[A-Za-z]*e[A-Za-z]*(?=\s|$|;)|-o\s+errexit\b)', re.MULTILINE)
_PIPEFAIL_PATTERN = re.compile(r'\bpipefail\b|\bPIPESTATUS\b')
# PowerShellのパスの区切りの `\` は、shlexではエスケープとして消える。英数字などの前の `\` は `/` に置き換えてから分ける。
# bashで意味を持つエスケープ（`\;`・`\|`・`\ `・`\"`・行末の `\` など）はそのまま残る。
_PATH_BACKSLASH_PATTERN = re.compile(r'\\(?=[\w.-])')
# 直後に否定が続く場合は主張とみなさない（例：「通っていない」「成功しなかった」）。
_JAPANESE_NEGATION = r'(?![^。\n]{0,6}?(?:ない|ません|なかった|ず))'
_TEST_CLAIM_PATTERNS = (
    re.compile(r'テスト[^。\n]{0,20}?(?:通過|通っ|通り|成功|パス|合格)' + _JAPANESE_NEGATION),
    re.compile(r'\b(?:all\s+)?tests?\s+(?:(?:are|were|now|all)\s+)*(?:pass(?:ed|es|ing)?|succeed(?:ed|s)?)\b', re.IGNORECASE),
)
_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
_QUOTE_LINE_PATTERN = re.compile(r'^\s*>.*$', re.MULTILINE)
_QUOTED_PATTERN = re.compile(r'`[^`\n]*`|「[^」\n]*」|『[^』\n]*』|"[^"\n]*"|“[^”\n]*”')


def compile_command_pattern(words):
    # コマンドの語のいずれかを含むかを判定する正規表現。`latest` などの語中は除く。
    return re.compile(r'(?<![a-z0-9])(?:' + '|'.join(words) + r')(?![a-z])', re.IGNORECASE)


_TEST_COMMAND_PATTERN = compile_command_pattern(TEST_COMMAND_WORDS)


def get_command_words(words):
    # 環境変数の代入とラッパーを読み飛ばし、実際に実行するコマンド以降の語を返す。
    index = 0
    while index < len(words):
        word = words[index]
        if _ASSIGNMENT_PATTERN.match(word) or word.startswith('-') or word in _WRAPPER_COMMANDS:
            index += 1
        elif word == 'timeout':
            index += 2
        else:
            break
    return words[index:]


def runs_command(words, pattern):
    # 単純コマンドの語が、読むだけのコマンドでなく、patternに一致するコマンドを実行するか。
    command_words = get_command_words(words)
    if not command_words or os.path.basename(command_words[0]).lower() in _READER_COMMANDS:
        return False
    return bool(pattern.search(' '.join(command_words)))


def _get_simple_commands(command):
    # 単純コマンドの語のリストと、直後の演算子（`&&`・`||`・`|`・`;`・`&`、最後のコマンドはNone）の組のリストを返す。
    # 改行は `;` として扱う。括弧は区切りとして読み飛ばす。
    commands = []
    pending = []
    for tokens in _split_bash_lines(_PATH_BACKSLASH_PATTERN.sub('/', command)):
        words = []
        index = 0
        while index <= len(tokens):
            token = tokens[index] if index < len(tokens) else ';'
            index += 1
            if token in _REDIRECT_OPERATORS:
                # `2>&1` の記述子、リダイレクト先、ヒアドキュメントの区切り語はコマンドの語ではない。
                if words and words[-1].isdigit():
                    words.pop()
                index += 1
                continue
            if not set(token) <= _OPERATOR_CHARACTERS:
                words.append(token)
                continue
            if words:
                pending.append(words)
                words = []
            operator = token.strip('()')
            if not operator:
                continue
            operator = '&&' if '&&' in operator else '||' if '||' in operator else '|' if '|' in operator else (
                '&' if '&' in operator else ';'
            )
            commands.extend((pending_words, operator) for pending_words in pending)
            pending = []
    commands.extend((pending_words, ';') for pending_words in pending)
    if commands and commands[-1][1] == ';':
        commands[-1] = (commands[-1][0], None)
    return commands


def _get_test_runs(command, pattern):
    # patternに一致するコマンドの実行ごとに、コマンド全体の終了コードがその結果を表すかを返す。
    # 後に `;`・`||` で別のコマンドが続く場合、バックグラウンドで実行する場合、pipefailなしでパイプの途中にある場合は、
    # 終了コードが結果を表さない。
    commands = _get_simple_commands(command)
    errexit = bool(_ERREXIT_PATTERN.search(command))
    pipefail = bool(_PIPEFAIL_PATTERN.search(command))
    results = []
    for index, (words, operator) in enumerate(commands):
        if not runs_command(words, pattern):
            continue
        reported = not (operator == '|' and not pipefail)
        for _, following in commands[index:]:
            if following in ('||', '&') or (following == ';' and not errexit):
                reported = False
                break
        results.append(reported)
    return results


def is_test_command(command, pattern=None):
    # コマンドがテスト（patternを指定した場合はそれに一致するコマンド）を実行するか。読むだけのコマンドは除く。
    return bool(_get_test_runs(command, pattern or _TEST_COMMAND_PATTERN))


def is_test_result_reported(command, pattern=None):
    # コマンドがテストを実行し、コマンド全体の終了コードがすべてのテストの結果を表すか。
    results = _get_test_runs(command, pattern or _TEST_COMMAND_PATTERN)
    return bool(results) and all(results)


def claims_tests_passed(response):
    # 出力例・引用・インラインコード内の表現は、応答自身の成功主張とみなさない。
    text = _FENCED_CODE_PATTERN.sub('\n', response)
    text = _QUOTE_LINE_PATTERN.sub('\n', text)
    text = _QUOTED_PATTERN.sub(' ', text)
    return any(pattern.search(text) for pattern in _TEST_CLAIM_PATTERNS)
