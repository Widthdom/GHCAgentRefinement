import os
import re
import sys

# コマンドがテストの実行か、応答が「テストが通った」と書いているかを判定する。
# テストの実行結果を扱うhook（verify_test_pass_claims・require_hook_tests・record_test_failure_values・detect_vacuous_test_success）と、
# テストを含む検証コマンドの終了コードを隠させないhook（block_masked_exit_codes）が共有する。

# コマンドの分割は共通のutilityで行う。utilityのフォルダーの親がutilitiesである。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'split_bash_lines'))
from split_bash_lines import split_bash_lines as _split_bash_lines

# テストの実行とみなすコマンドの語（正規表現）。`test_all_hooks.py`・`npm test`・`pytest` などを含む。
TEST_COMMAND_WORDS = ('tests?', 'pytest', 'unittest', 'jest', 'vitest', 'mocha', 'rspec', 'phpunit', 'ctest', 'tox', 'pester')
# 出力やファイルを読むだけのコマンド、移動するだけのコマンド、ファイルを作成・コピー・移動・削除するだけのコマンド。
# `grep test`・`ls tests`・`cd tests`・`mkdir tests` をテストの実行とみなさない。
# PowerShellのコマンドレットは大文字小文字を区別せずに照合するため、小文字で書く。
_READER_COMMANDS = {
    'ag', 'awk', 'cat', 'cd', 'chmod', 'cp', 'cut', 'diff', 'du', 'echo', 'egrep', 'fd', 'fgrep', 'file', 'find',
    'git', 'grep', 'head', 'jq', 'less', 'ln', 'ls', 'mkdir', 'more', 'mv', 'printf', 'pushd', 'rg', 'rm', 'rmdir',
    'sed', 'sort', 'stat', 'tail', 'tee', 'touch', 'tr', 'tree', 'uniq', 'wc', 'which', 'yq',
    'get-childitem', 'get-content', 'select-string', 'set-location', 'write-host', 'write-output',
}
# 条件を判定する組み込みコマンド。`test -f x`・`[ -d tests ]` をテストの実行とみなさない。
# パスを付けた `./test` は、テストのスクリプトの実行とみなすため、語そのものだけを照合する。
_CONDITION_COMMANDS = {'test', '[', '[['}
# 後続の語が実際のコマンドになるもの。`timeout` は次の時間指定も読み飛ばす。
_WRAPPER_COMMANDS = {'builtin', 'command', 'env', 'exec', 'nice', 'nohup', 'sudo', 'time', 'xargs'}
_ASSIGNMENT_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_REDIRECT_OPERATORS = {'>', '>>', '>|', '&>', '&>>', '<', '<<', '<<-', '<<<', '<&', '>&', '<>'}
_OPERATOR_CHARACTERS = frozenset('();&|<>')
# `set -e`・`set -euo pipefail`・`set -o errexit` があれば、後続のコマンドの失敗で終了する。
_ERREXIT_PATTERN = re.compile(r'(?:^|[\s;&|(])set\s+(?:-[A-Za-z]*e[A-Za-z]*(?=\s|$|;)|-o\s+errexit\b)', re.MULTILINE)
# `set -o pipefail`・`set -euo pipefail` などの `o` を含むオプションの直後の語がpipefailなら設定とみなす。
_SET_OPTION_PATTERN = re.compile(r'^([-+])[A-Za-z]*o$')
# PowerShellのパスの区切りの `\` は、shlexではエスケープとして消える。英数字などの前の `\` は `/` に置き換えてから分ける。
# bashで意味を持つエスケープ（`\;`・`\|`・`\ `・`\"`・行末の `\` など）はそのまま残る。
_PATH_BACKSLASH_PATTERN = re.compile(r'\\(?=[\w.-])')
# 直後に否定が続く場合は主張とみなさない（例：「通っていない」「成功しなかった」「通らず」）。
# 「まず」「必ず」の `ず` は否定ではないため除く。
# テストの追加の主張を判定するhook（block_commit_message_mismatch）も同じ基準を使う。
JAPANESE_NEGATION = r'(?![^。\n]{0,6}?(?:ない|ません|なかった|(?<![ま必])ず))'
_TEST_CLAIM_PATTERNS = (
    re.compile(r'テスト[^。\n]{0,20}?(?:通過|通っ|通り|成功|パス|合格)' + JAPANESE_NEGATION),
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
    if (not command_words or command_words[0] in _CONDITION_COMMANDS or
            os.path.basename(command_words[0]).lower() in _READER_COMMANDS):
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


def update_pipefail(words, current):
    # 単純コマンドの語が `set -o pipefail` なら有効、`set +o pipefail` なら無効にした状態を返す。それ以外は変えない。
    if not words or words[0] != 'set':
        return current
    for option, following in zip(words[1:], words[2:]):
        match = _SET_OPTION_PATTERN.match(option)
        if match and following == 'pipefail':
            current = match.group(1) == '-'
    return current


# 終了コードが結果を表さない理由。
MASKED_BY_PIPE = 'pipe'
MASKED_BY_FOLLOWING_COMMAND = 'following command'


def _get_test_runs(command, pattern):
    # patternに一致するコマンドの実行ごとに、(単純コマンドの語のリスト, 終了コードが結果を表さない理由) を返す。
    # 理由は、pipefailなしでパイプの途中にある場合は `MASKED_BY_PIPE`、
    # 後に `;`・`||` で別のコマンドが続く場合とバックグラウンドで実行する場合は `MASKED_BY_FOLLOWING_COMMAND`、結果を表す場合はNone。
    # pipefailは、それより前の `set -o pipefail`・`set +o pipefail` を順に追って判定する。
    # `PIPESTATUS` を参照している場合は、各段の終了コードを確かめているとみなす。
    commands = _get_simple_commands(command)
    errexit = bool(_ERREXIT_PATTERN.search(command))
    uses_pipestatus = 'PIPESTATUS' in command
    pipefail = False
    results = []
    for index, (words, operator) in enumerate(commands):
        if runs_command(words, pattern):
            reason = MASKED_BY_PIPE if operator == '|' and not pipefail and not uses_pipestatus else None
            if reason is None:
                for _, following in commands[index:]:
                    if following in ('||', '&') or (following == ';' and not errexit):
                        reason = MASKED_BY_FOLLOWING_COMMAND
                        break
            results.append((words, reason))
        # パイプの中の `set` はサブシェルで実行されるため、後続のコマンドに効かない。
        if operator != '|' and (index == 0 or commands[index - 1][1] != '|'):
            pipefail = update_pipefail(words, pipefail)
    return results


def is_test_command(command, pattern=None):
    # コマンドがテスト（patternを指定した場合はそれに一致するコマンド）を実行するか。読むだけのコマンドは除く。
    return bool(_get_test_runs(command, pattern or _TEST_COMMAND_PATTERN))


def is_test_result_reported(command, pattern=None):
    # コマンドがテストを実行し、コマンド全体の終了コードがすべてのテストの結果を表すか。
    results = _get_test_runs(command, pattern or _TEST_COMMAND_PATTERN)
    return bool(results) and all(reason is None for _, reason in results)


def get_masked_test_runs(command, pattern=None):
    # 終了コードが結果を表さないテスト（patternを指定した場合はそれに一致するコマンド）の実行ごとに、
    # (単純コマンドの語のリスト, 理由) を返す。理由は `MASKED_BY_PIPE` か `MASKED_BY_FOLLOWING_COMMAND`。
    return [(words, reason) for words, reason in _get_test_runs(command, pattern or _TEST_COMMAND_PATTERN) if reason]


def claims_tests_passed(response):
    # 出力例・引用・インラインコード内の表現は、応答自身の成功主張とみなさない。
    text = _FENCED_CODE_PATTERN.sub('\n', response)
    text = _QUOTE_LINE_PATTERN.sub('\n', text)
    text = _QUOTED_PATTERN.sub(' ', text)
    return any(pattern.search(text) for pattern in _TEST_CLAIM_PATTERNS)
