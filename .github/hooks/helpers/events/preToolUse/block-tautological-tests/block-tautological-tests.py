import argparse
import builtins
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from contextlib import closing
from typing import cast


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
# 置換前の内容を表す引数。書き込む内容ではないため検査せず、既存の内容として照合に使う。
REMOVED_TEXT_KEYS = {'old_str', 'old_string'}
# 書き込む内容ではない引数。
IGNORED_KEYS = {'path', 'command'}
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+?)\s*$')
TEST_FILE_PATTERNS_KEY = 'testFilePatterns'

# 2つの値を比べるアサーションの呼び出し。最初の2引数を比べる。
TWO_ARGUMENT_ASSERTION_PATTERN = re.compile(
    r'(?<![\w.])(?:'
    r'Assert\.(?:Equal|AreEqual|StrictEqual|Same|AreSame|Equivalent)'
    r'|(?:self\.)?assert(?:Equals?|Is|AlmostEqual|ListEqual|TupleEqual|SetEqual|DictEqual|SequenceEqual|CountEqual|MultiLineEqual|Same)'
    r'|assert\.(?:equal|strictEqual|deepEqual|deepStrictEqual)'
    r'|assert_eq!'
    r')\s*\('
)
# NUnitの制約モデル `Assert.That(actual, Is.EqualTo(expected))`。
ASSERT_THAT_PATTERN = re.compile(r'(?<![\w.])Assert\.That\s*\(')
IS_EQUAL_TO_PATTERN = re.compile(r'^Is\.(?:EqualTo|SameAs|EquivalentTo)\s*\(')
# Jestなどの `expect(actual).toBe(expected)`。
EXPECT_PATTERN = re.compile(r'(?<![\w.])expect\s*\(')
EXPECT_MATCHER_PATTERN = re.compile(r'\s*\.\s*(?:toBe|toEqual|toStrictEqual)\s*\(')
# FluentAssertionsの `actual.Should().Be(expected)`。
SHOULD_PATTERN = re.compile(
    r'^\s*(?:await\s+)?(?P<actual>.+?)\s*\.\s*Should\s*\(\s*\)\s*\.\s*(?:Be|BeEquivalentTo|Equal|BeSameAs)\s*\('
)
# Pythonの `assert actual == expected`。
PYTHON_ASSERT_PATTERN = re.compile(r'^\s*assert\s+(?P<body>.+?)\s*$')
# 変数への代入。型名・宣言キーワード・型注釈を許容し、比較（`==`）や複合代入は除く。
ASSIGNMENT_PATTERN = re.compile(
    r'^\s*(?:(?:var|let|const|val|auto|final|dim)\s+|[A-Za-z_][\w<>\[\],.?]*\s+)?'
    r'(?P<name>[A-Za-z_]\w*)\s*(?::\s*[^=]+?)?\s*=(?!=)\s*(?P<value>.+?)\s*;?\s*$'
)
ASSIGNMENT_EXCLUDED_PREFIXES = ('if ', 'elif ', 'while ', 'for ', 'return ', 'assert ', 'yield ')

# Pythonのテスト関数と、その中でテスト対象を差し替えるモック。
PYTHON_TEST_FUNCTION_PATTERN = re.compile(r'^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>test_\w+)\s*\(')
PYTHON_PATCH_PATTERNS = [
    # patch('module.target')・mock.patch(...)・mocker.patch(...)
    re.compile(r'(?<![\w.])(?:mock\.|mocker\.|unittest\.mock\.)?patch\s*\(\s*[\'"](?P<target>[\w.]+)[\'"]'),
    # patch.object(Owner, 'target')・mocker.patch.object(...)
    re.compile(r'(?<![\w.])(?:mock\.|mocker\.|unittest\.mock\.)?patch\.object\s*\(\s*(?P<owner>[\w.]+)\s*,\s*[\'"](?P<target>\w+)[\'"]'),
    # monkeypatch.setattr('module.target', ...)・monkeypatch.setattr(owner, 'target', ...)
    re.compile(r'\bmonkeypatch\.setattr\s*\(\s*[\'"](?P<target>[\w.]+)[\'"]'),
    re.compile(r'\bmonkeypatch\.setattr\s*\(\s*(?P<owner>[\w.]+)\s*,\s*[\'"](?P<target>\w+)[\'"]'),
]
# 標準ライブラリの差し替えは依存の置き換えであり、テスト対象の差し替えではない。
STANDARD_PATCH_OWNERS = {'builtins', 'os', 'sys', 'time', 'datetime', 'random', 'subprocess', 'socket', 'shutil', 'io'}
MIN_TARGET_LENGTH = 3

TAUTOLOGY = 'tautology'
SELF_MOCK = 'self-mock'
TAUTOLOGY_REASON = '{file} would get assertions that compare a value with itself ({assertions}).'
SELF_MOCK_REASON = '{file} would get tests that mock the function they test ({mocks}).'
DENY_REASON = (
    'Edit denied: {findings} Such a test passes no matter what the implementation does, so it verifies nothing. '
    'Compute the expected value independently, for example as a literal worked out by hand or from the specification, '
    'and call the real implementation; mock only its dependencies. '
    'If the comparison is intended, for example a test of reflexive Equals or of determinism, retry the same edit; '
    'this check denies it only once.'
)


def compile_patterns(patterns, key):
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {key}: {ex}') from None


def load_test_file_patterns(path):
    # テストファイルの判定はblock-test-weakening hookの定義を共有し、二重管理しない。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    patterns = definitions.get(TEST_FILE_PATTERNS_KEY) if isinstance(definitions, dict) else None
    if not isinstance(patterns, list) or not patterns or not all(
        isinstance(pattern, str) and pattern.strip() for pattern in patterns
    ):
        raise ValueError(f'test file definitions require a non-empty array of non-empty strings for {TEST_FILE_PATTERNS_KEY}.')
    return compile_patterns(patterns, TEST_FILE_PATTERNS_KEY)


def get_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列（apply_patch）で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def collect_strings(value, ignored_keys=frozenset()):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in ignored_keys:
                yield from collect_strings(item, ignored_keys)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item, ignored_keys)


def get_patch_writes(text):
    # パッチをファイルごとに分け、追加行（`+`）を書き込む内容、削除行（`-`）を置換前の内容とする。
    writes = []
    for line in text.splitlines():
        match = PATCH_FILE_PATTERN.match(line)
        if match:
            writes.append({'path': match.group(2), 'written': [], 'removed': [], 'deleted': match.group(1) == 'Delete'})
        elif writes and line.startswith('+'):
            writes[-1]['written'].append(line[1:])
        elif writes and line.startswith('-'):
            writes[-1]['removed'].append(line[1:])
    return [
        {'path': write['path'], 'written': '\n'.join(write['written']), 'removed': '\n'.join(write['removed'])}
        for write in writes if not write['deleted']
    ]


def get_writes(tool_name, tool_args):
    # 戻り値は {path, written（書き込む内容）, removed（置換前の内容）} のリスト。
    if tool_name == 'apply_patch':
        return [write for text in collect_strings(tool_args) for write in get_patch_writes(text)]
    if not isinstance(tool_args, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    if not isinstance(path, str) or not path.strip():
        return []
    return [{
        'path': path,
        'written': '\n'.join(collect_strings(tool_args, IGNORED_KEYS | REMOVED_TEXT_KEYS)),
        'removed': '\n'.join(text for key in REMOVED_TEXT_KEYS for text in collect_strings(tool_args.get(key))),
    }]


def read_existing(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()
    except OSError:
        return None


def get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path


def is_test_file(test_file_patterns, display_path):
    # パターンはOSによらず `/` 区切りのパスに対して照合する。
    slash_path = display_path.replace(os.sep, '/')
    return any(pattern.search(slash_path) for pattern in test_file_patterns)


def normalize(text):
    # 書式の違いで同じ式を別物と判定しないよう、空白を除いて比べる。
    return re.sub(r'\s+', '', text)


def split_arguments(text, open_index):
    # text[open_index] の `(` から対応する `)` までを、トップレベルのカンマで分ける。
    # 文字列リテラル内の括弧とカンマは無視する。戻り値は (引数のリスト, `)` の次の位置)。閉じなければNone。
    depth = 0
    quote = None
    arguments = []
    start = open_index + 1
    index = open_index
    while index < len(text):
        character = text[index]
        if quote:
            if character == '\\':
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in '"\'`':
            quote = character
        elif character in '([{':
            depth += 1
        elif character in ')]}':
            depth -= 1
            if depth == 0:
                arguments.append(text[start:index].strip())
                return [argument for argument in arguments if argument], index + 1
        elif character == ',' and depth == 1:
            arguments.append(text[start:index].strip())
            start = index + 1
        index += 1
    return None


def split_top_level(text, separator_pattern):
    # 括弧と文字列リテラルの外で、最初に一致した区切りの前後に分ける。区切りがなければNone。
    depth = 0
    quote = None
    index = 0
    while index < len(text):
        character = text[index]
        if quote:
            if character == '\\':
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in '"\'':
            quote = character
        elif character in '([{':
            depth += 1
        elif character in ')]}':
            depth -= 1
        elif depth == 0:
            match = separator_pattern.match(text, index)
            if match:
                return text[:index], text[match.end():]
        index += 1
    return None


def find_compared_pairs(text):
    # 戻り値は (アサーションの原文, 比べる値1, 比べる値2) のリスト。
    pairs = []
    for match in TWO_ARGUMENT_ASSERTION_PATTERN.finditer(text):
        parsed = split_arguments(text, match.end() - 1)
        if parsed and len(parsed[0]) >= 2:
            pairs.append((text[match.start():parsed[1]], parsed[0][0], parsed[0][1]))
    for match in ASSERT_THAT_PATTERN.finditer(text):
        parsed = split_arguments(text, match.end() - 1)
        if not parsed or len(parsed[0]) < 2:
            continue
        constraint = IS_EQUAL_TO_PATTERN.match(parsed[0][1])
        inner = split_arguments(parsed[0][1], constraint.end() - 1) if constraint else None
        if inner and inner[0]:
            pairs.append((text[match.start():parsed[1]], parsed[0][0], inner[0][0]))
    for match in EXPECT_PATTERN.finditer(text):
        parsed = split_arguments(text, match.end() - 1)
        if not parsed or len(parsed[0]) != 1:
            continue
        matcher = EXPECT_MATCHER_PATTERN.match(text, parsed[1])
        expected = split_arguments(text, matcher.end() - 1) if matcher else None
        if expected and expected[0]:
            pairs.append((text[match.start():expected[1]], parsed[0][0], expected[0][0]))
    for line in text.splitlines():
        match = SHOULD_PATTERN.match(line)
        if match:
            parsed = split_arguments(line, match.end() - 1)
            if parsed and parsed[0]:
                pairs.append((line[match.start('actual'):parsed[1]], match.group('actual'), parsed[0][0]))
            continue
        match = PYTHON_ASSERT_PATTERN.match(line)
        if match:
            # `assert a == b, "message"` のメッセージを除いてから比べる。
            body = match.group('body')
            body = (split_top_level(body, re.compile(r',')) or (body, ''))[0]
            compared = split_top_level(body, re.compile(r'\s*(?:==|\bis\b(?!\s+not\b))\s*'))
            if compared:
                pairs.append((line.strip(), compared[0], compared[1]))
    return pairs


def get_assignments(text):
    # 1回だけ代入された変数の、正規化した値。再代入される変数は、どの値を指すか決まらないため除く。
    values = {}
    counts = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(ASSIGNMENT_EXCLUDED_PREFIXES):
            continue
        match = ASSIGNMENT_PATTERN.match(line)
        if match:
            name = match.group('name')
            counts[name] = counts.get(name, 0) + 1
            values[name] = normalize(match.group('value'))
    return {name: value for name, value in values.items() if counts[name] == 1}


def resolve(value, assignments):
    # 変数なら代入された値に置き換える。代入の連鎖は数段までたどる。
    normalized = normalize(value)
    for _ in range(3):
        if normalized not in assignments:
            break
        normalized = assignments[normalized]
    return normalized


def find_tautologies(written, context):
    # 新たに書き込むアサーションのうち、比べる2つの値が同じ式になるもの。
    # context（置換前の内容とファイルの現在の内容）に同じアサーションがあれば、元からあるものとみなす。
    assignments = get_assignments('\n'.join([context, written]))
    existing = normalize(context)
    tautologies = []
    for source, left, right in find_compared_pairs(written):
        normalized_source = normalize(source)
        source = ' '.join(source.split())
        if normalized_source in existing or source in tautologies:
            continue
        if resolve(left, assignments) == resolve(right, assignments):
            tautologies.append(source)
    return tautologies


def get_patched_target(line):
    # 戻り値は差し替える名前。標準ライブラリの差し替えはNone。
    for pattern in PYTHON_PATCH_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        groups = match.groupdict()
        target = groups['target']
        owner = groups.get('owner')
        if owner is None:
            owner, _, target = target.rpartition('.')
        if owner.split('.')[0] in STANDARD_PATCH_OWNERS or target in dir(builtins):
            return None
        return target
    return None


def find_self_mocks(written, context):
    # Pythonのテスト関数 `test_<対象>...` の中（デコレーターを含む）で、`<対象>` 自身を差し替える行。
    # 関数の範囲はインデントで判断し、関数の行以下のインデントの行で抜けたとみなす。
    existing_lines = {line.strip() for line in context.splitlines()}
    self_mocks = []
    pending_decorators = []
    # (テスト対象の名前, テスト関数のインデント)
    current = None
    for line in written.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        function = PYTHON_TEST_FUNCTION_PATTERN.match(line)
        if function:
            current = (function.group('name').removeprefix('test_').lower(), indent)
            checked_lines = pending_decorators
            pending_decorators = []
        else:
            if current and indent <= current[1]:
                current = None
            if stripped.startswith('@'):
                pending_decorators.append(line)
                continue
            pending_decorators = []
            checked_lines = [line]
        if not current:
            continue
        for body_line in checked_lines:
            target = get_patched_target(body_line)
            if (
                target and len(target) >= MIN_TARGET_LENGTH
                and (current[0] == target.lower() or current[0].startswith(f'{target.lower()}_'))
                and body_line.strip() not in existing_lines and body_line.strip() not in self_mocks
            ):
                self_mocks.append(body_line.strip())
    return self_mocks


def record_denials(state_path, candidates):
    # candidates：(ファイルキー, 種類, 理由) のリスト。戻り値は、今回初めて拒否する理由のリスト。
    # 同じファイルの同じ種類の拒否は1回だけにし、意図した編集なら再実行で通す。
    # SQLiteのトランザクションで、並列のツール呼び出しでも重複して拒否しない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    reasons = []
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS denials (file TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (file, kind))'
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for file_key, kind, reason in candidates:
                if connection.execute(
                    'INSERT OR IGNORE INTO denials (file, kind) VALUES (?, ?)', (file_key, kind),
                ).rowcount:
                    reasons.append(reason)
    return reasons


def format_findings(findings):
    return ', '.join(f'"{finding}"' for finding in findings[:3])


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    script_directory = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(
            os.path.dirname(script_directory), 'block-test-weakening', 'test-weakening-definitions.json',
        ),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        test_file_patterns = load_test_file_patterns(parsed_arguments.definitions_path)

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

        real_cwd = os.path.realpath(cwd)
        candidates = []
        tool_name = tool_name.removeprefix('functions.')
        if tool_name in EDIT_TOOL_NAMES:
            for write in get_writes(tool_name, get_tool_args(hook_input['toolArgs'])):
                path = os.path.realpath(os.path.join(cwd, write['path']))
                # テストファイルの判定は、Windowsのnormcaseで小文字にする前のパスで行い、大文字小文字を区別できるようにする。
                display_path = get_display_path(path, real_cwd)
                if not is_test_file(test_file_patterns, display_path):
                    continue
                # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                file_key = hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()
                current = (read_existing(path) if os.path.isfile(path) else None) or ''
                context = '\n'.join([write['removed'], current])

                tautologies = find_tautologies(write['written'], context)
                if tautologies:
                    candidates.append((file_key, TAUTOLOGY, TAUTOLOGY_REASON.format(
                        file=display_path, assertions=format_findings(tautologies),
                    )))
                self_mocks = find_self_mocks(write['written'], context)
                if self_mocks:
                    candidates.append((file_key, SELF_MOCK, SELF_MOCK_REASON.format(
                        file=display_path, mocks=format_findings(self_mocks),
                    )))

        reasons = []
        if candidates:
            # hookは呼び出しごとに別プロセスなので、拒否済みのファイルをセッションUUID別に保存する。
            # DBファイル自体は自動削除しない。
            state_path = os.path.join(
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-tautological-tests',
                f'{parsed_session_id}.sqlite3',
            )
            reasons = record_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check tautological tests: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = DENY_REASON.format(findings=' '.join(reasons))
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
