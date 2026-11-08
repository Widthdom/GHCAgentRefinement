import re

# コマンドがテストの実行か、応答が「テストが通った」と書いているかを判定する。
# テストの実行結果を扱うhook（verify-test-pass-claims・record-test-failure-values・detect-vacuous-test-success）と、
# テストを含む検証コマンドの終了コードを隠させないhook（block-masked-exit-codes）が共有する。

# テストの実行とみなすコマンドの語（正規表現）。`test-all-hooks.py`・`npm test`・`pytest` などを含む。
TEST_COMMAND_WORDS = ('tests?', 'pytest', 'unittest', 'jest', 'vitest', 'mocha', 'rspec', 'phpunit', 'ctest', 'tox', 'pester')
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


def is_test_command(command):
    return _TEST_COMMAND_PATTERN.search(command) is not None


def claims_tests_passed(response):
    # 出力例・引用・インラインコード内の表現は、応答自身の成功主張とみなさない。
    text = _FENCED_CODE_PATTERN.sub('\n', response)
    text = _QUOTE_LINE_PATTERN.sub('\n', text)
    text = _QUOTED_PATTERN.sub(' ', text)
    return any(pattern.search(text) for pattern in _TEST_CLAIM_PATTERNS)
