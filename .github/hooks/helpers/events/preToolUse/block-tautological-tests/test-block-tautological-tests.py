import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-tautological-tests.py')
_state_directory = os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-tautological-tests')

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_ORIGINAL = (
    'def test_add():\n'
    '    assert add(1, 2) == 3\n'
)


def _run_hook(input_bytes, *arguments):
    return subprocess.run([sys.executable, _hook_path, *arguments], input=input_bytes, capture_output=True)


class BlockTautologicalTestsTests(unittest.TestCase):
    def setUp(self):
        self.directory = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.new_session()
        self.write_file('test_math.py', _ORIGINAL)

    def write_file(self, path, content):
        full_path = os.path.join(self.directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def new_session(self):
        # テストごとに別セッションとし、拒否の記録を持ち越さない。
        self.session_id = str(uuid.uuid4())
        state_path = os.path.join(_state_directory, f'{self.session_id}.sqlite3')
        self.addCleanup(lambda: os.path.exists(state_path) and os.remove(state_path))

    def run_call(self, tool_name, tool_args):
        hook_input = {'sessionId': self.session_id, 'cwd': self.directory, 'toolName': tool_name, 'toolArgs': tool_args}
        return _run_hook(json.dumps(hook_input).encode('utf-8'))

    def create(self, path, content):
        return self.run_call('create', {'path': path, 'file_text': content})

    def assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)

    def test_self_comparisons_are_denied(self):
        cases = [
            ('CalcTests.cs', 'Assert.Equal(sut.Add(1, 2), sut.Add(1,2));', 'Assert.Equal(sut.Add(1, 2), sut.Add(1,2))'),
            ('CalcTests.cs', 'Assert.AreEqual(calc.Total, calc.Total);', 'Assert.AreEqual(calc.Total, calc.Total)'),
            ('CalcTests.cs', 'Assert.That(x.Foo(), Is.EqualTo(x.Foo()));', 'Assert.That(x.Foo(), Is.EqualTo(x.Foo()))'),
            ('CalcTests.cs', 'result.Should().Be(result);', 'result.Should().Be(result)'),
            ('CalcTest.java', 'assertEquals(calc.add(1, 2), calc.add(1, 2));', 'assertEquals(calc.add(1, 2), calc.add(1, 2))'),
            ('test_math.py', 'assert parse("a") == parse("a"), "same"', 'assert parse("a") == parse("a"), "same"'),
            ('test_math.py', 'self.assertEqual(f(x), f(x))', 'self.assertEqual(f(x), f(x))'),
            ('test_math.py', 'assert value is value', 'assert value is value'),
            ('src/app.test.ts', 'expect(add(1, 2)).toBe(add(1, 2));', 'expect(add(1, 2)).toBe(add(1, 2))'),
            ('src/app.test.js', 'assert.deepStrictEqual(parse(s), parse(s));', 'assert.deepStrictEqual(parse(s), parse(s))'),
            ('tests/calc.rs', 'assert_eq!(add(1, 2), add(1, 2));', 'assert_eq!(add(1, 2), add(1, 2))'),
        ]
        for path, line, finding in cases:
            with self.subTest(path=path, line=line):
                self.new_session()
                self.assert_denied(
                    self.create(path, f'{line}\n'), [path, f'"{finding}"', 'compare a value with itself'], line,
                )

    def test_expected_value_from_the_same_call_is_denied(self):
        cases = [
            ('CalcTests.cs', 'var expected = calc.Add(1, 2);\nAssert.Equal(expected, calc.Add(1, 2));\n'),
            ('test_math.py', 'expected = parse("a")\nactual = parse("a")\nassert actual == expected\n'),
            ('src/app.test.ts', 'const expected = add(1, 2);\nexpect(add(1, 2)).toEqual(expected);\n'),
        ]
        for path, content in cases:
            with self.subTest(path=path):
                self.new_session()
                self.assert_denied(self.create(path, content), [path, 'compare a value with itself'], content)

    def test_multiline_assertion_is_denied(self):
        self.assert_denied(
            self.create('CalcTests.cs', 'Assert.Equal(\n    calc.Add(1, 2),\n    calc.Add(1, 2));\n'),
            ['"Assert.Equal( calc.Add(1, 2), calc.Add(1, 2))"'], 'multiline',
        )

    def test_independent_expected_values_are_allowed(self):
        contents = [
            'Assert.Equal(3, sut.Add(1, 2));\n',
            'Assert.That(x.Foo(), Is.EqualTo(42));\n',
            'result.Should().Be(expected);\n',
            'assert parse("a") == ["a"]\n',
            'assert value is not value.copy()\n',
            'expect(add(1, 2)).toBe(3);\n',
            # 再代入される変数は、どの値を指すか決まらないため比べない。
            'x = compute()\nx = compute()\nassert x == compute()\n',
            'expected = 3\nassert add(1, 2) == expected\n',
        ]
        for content in contents:
            with self.subTest(content=content):
                self.new_session()
                self.assert_allowed(self.create('test_math.py', content), content)

    def test_self_mocks_are_denied(self):
        cases = [
            ('@patch("app.parser.parse")\ndef test_parse_empty(mock_parse):\n    assert parse("") == []\n', '@patch("app.parser.parse")'),
            ('def test_parse(mocker):\n    mocker.patch.object(Parser, "parse", return_value=[])\n',
             'mocker.patch.object(Parser, "parse", return_value=[])'),
            ('def test_load(monkeypatch):\n    monkeypatch.setattr("app.config.load", lambda: {})\n',
             'monkeypatch.setattr("app.config.load", lambda: {})'),
            ('class TestParser:\n    def test_parse(self):\n        with patch("app.parse"):\n            pass\n',
             'with patch("app.parse"):'),
        ]
        for content, finding in cases:
            with self.subTest(content=content):
                self.new_session()
                self.assert_denied(
                    self.create('test_parser.py', content),
                    ['test_parser.py', f'"{finding}"', 'mock the function they test'], content,
                )

    def test_dependency_mocks_are_allowed(self):
        contents = [
            'def test_parse(mocker):\n    mocker.patch("app.io.read_file", return_value="a")\n',
            'def test_open_config(mocker):\n    mocker.patch("builtins.open")\n',
            'def test_sleep(mocker):\n    mocker.patch("time.sleep")\n',
            # テスト関数を抜けた後の差し替えは対象外。
            'def test_load():\n    pass\n\ndef helper():\n    patch("app.load")\n',
            # 短すぎる名前は偶然の一致とみなす。
            'def test_io_x(mocker):\n    mocker.patch("app.io")\n',
        ]
        for content in contents:
            with self.subTest(content=content):
                self.new_session()
                self.assert_allowed(self.create('test_parser.py', content), content)

    def test_existing_assertions_are_allowed(self):
        self.write_file('CalcTests.cs', 'Assert.Equal(calc.Total, calc.Total);\n')
        self.assert_allowed(
            self.run_call('edit', {
                'path': 'CalcTests.cs', 'old_str': 'Assert.Equal(calc.Total, calc.Total);',
                'new_str': '// keep\nAssert.Equal(calc.Total,  calc.Total);',
            }),
            'existing assertion',
        )

    def test_non_test_files_are_allowed(self):
        self.assert_allowed(self.create('src/calc.py', 'assert a == a\n'), 'non-test file')

    def test_denies_only_once_per_file_and_kind(self):
        tool_args = {'path': 'test_math.py', 'file_text': 'assert f(x) == f(x)\n'}
        self.assert_denied(self.run_call('create', tool_args), ['compare a value with itself'], 'first')
        self.assert_allowed(self.run_call('create', tool_args), 'second')

    def test_apply_patch_is_checked(self):
        patch = (
            '*** Begin Patch\n*** Update File: test_math.py\n@@\n'
            ' def test_add():\n-    assert add(1, 2) == 3\n+    assert add(1, 2) == add(1, 2)\n*** End Patch\n'
        )
        self.assert_denied(self.run_call('apply_patch', patch), ['test_math.py'], 'patch')

    def test_view_and_other_tools_are_skipped(self):
        self.assert_allowed(self.run_call('str_replace_editor', {'command': 'view', 'path': 'test_math.py'}), 'view')
        self.assert_allowed(self.run_call('bash', {'command': 'echo "assert a == a" > test_x.py'}), 'bash')

    def test_invalid_input_fails(self):
        inputs = [
            b'',
            b'[]',
            json.dumps({'sessionId': 'x', 'cwd': self.directory, 'toolName': 'create', 'toolArgs': {}}).encode(),
            json.dumps({'sessionId': self.session_id, 'cwd': 'relative', 'toolName': 'create', 'toolArgs': {}}).encode(),
            json.dumps({'sessionId': self.session_id, 'cwd': self.directory, 'toolArgs': {}}).encode(),
            json.dumps({'sessionId': self.session_id, 'cwd': self.directory, 'toolName': 'create'}).encode(),
        ]
        for input_bytes in inputs:
            with self.subTest(input_bytes=input_bytes):
                result = _run_hook(input_bytes)
                self.assertEqual(result.returncode, 2, input_bytes)
                self.assertIn(b'Failed to check tautological tests', result.stderr)

    def test_invalid_definitions_fail(self):
        definitions_path = os.path.join(self.directory, 'definitions.json')
        for content in ['{', '[]', '{"testFilePatterns": []}', '{"testFilePatterns": ["("]}']:
            with self.subTest(content=content):
                with open(definitions_path, 'w', encoding='utf-8') as file:
                    file.write(content)
                hook_input = {'sessionId': self.session_id, 'cwd': self.directory, 'toolName': 'create', 'toolArgs': {}}
                result = _run_hook(json.dumps(hook_input).encode('utf-8'), '--definitions-path', definitions_path)
                self.assertEqual(result.returncode, 2, content)


if __name__ == '__main__':
    run_tests()
