import re

# コマンドがテストの実行か、応答が「テストが通った」と書いているかを判定する。
# テストの実行結果を扱うhook（verify-completion-claims・detect-stale-verification・record-test-failure-values・
# detect-vacuous-test-success）と、テストを含む検証コマンドの終了コードを隠させないhook（block-masked-exit-codes）が共有する。

# テストの実行とみなすコマンドの語（正規表現）。`test-all-hooks.py`・`npm test`・`pytest` などを含む。
TEST_COMMAND_WORDS = ('tests?', 'pytest', 'unittest', 'jest', 'vitest', 'mocha', 'rspec', 'phpunit', 'ctest', 'tox', 'pester')
# 直後に否定が続く場合は主張とみなさない（例：「通っていない」「成功しなかった」）。
JAPANESE_NEGATION = r'(?![^。\n]{0,6}?(?:ない|ません|なかった|ず))'
TEST_CLAIM_PATTERNS = (
    re.compile(r'テスト[^。\n]{0,20}?(?:通過|通っ|通り|成功|パス|合格)' + JAPANESE_NEGATION),
    re.compile(r'\b(?:all\s+)?tests?\s+(?:(?:are|were|now|all)\s+)*(?:pass(?:ed|es|ing)?|succeed(?:ed|s)?)\b', re.IGNORECASE),
)
FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)


def compile_command_pattern(words):
    # コマンドの語のいずれかを含むかを判定する正規表現。`latest` などの語中は除く。
    return re.compile(r'(?<![a-z0-9])(?:' + '|'.join(words) + r')(?![a-z])', re.IGNORECASE)


TEST_COMMAND_PATTERN = compile_command_pattern(TEST_COMMAND_WORDS)


def is_test_command(command):
    return TEST_COMMAND_PATTERN.search(command) is not None


def claims_tests_passed(response):
    # コードブロック内の出力例やコマンドは主張とみなさない。
    text = FENCED_CODE_PATTERN.sub('', response)
    return any(pattern.search(text) for pattern in TEST_CLAIM_PATTERNS)
