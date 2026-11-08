import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-time-bomb-literals.py')
_definitions_path = os.path.join(_script_directory, 'time-bomb-definitions.json')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

_get_hook_entry = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-entry.py'))['get_hook_entry']

_TODAY = '2026-11-03'


class BlockTimeBombLiteralsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-time-bomb-literals-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})
        self.__directory = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__directory)
        self.__write_file('tests/test_expiry.py', 'from datetime import date\n\n\ndef test_expiry():\n    pass\n')
        self.__write_file('src/expiry.py', 'def is_expired(day):\n    return False\n')
        self.__new_session()

    def __write_file(self, path, content):
        full_path = os.path.join(self.__directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __new_session(self):
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__root, 'ghc-agent-refinement', 'hook-states', 'block-time-bomb-literals', f'{self.__session_id}.sqlite3',
        )

    def __run_hook(self, hook_input, *arguments):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path, '--today', _TODAY, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __run_call(self, tool_name, tool_args):
        return self.__run_hook({
            'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': tool_name, 'toolArgs': tool_args,
        })

    def __assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)

    def test_definitions_are_valid_json(self):
        with open(_definitions_path, encoding='utf-8') as file:
            definitions = json.load(file)
        self.assertTrue(definitions['clockPatterns'])
        self.assertTrue(definitions['frozenClockPatterns'])

    def test_configuration(self):
        entry = _get_hook_entry(_hook_path)
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertRegex(tool_name, f'^(?:{entry["matcher"]})$')
        self.assertNotRegex('bash', f'^(?:{entry["matcher"]})$')

    def test_near_dates_compared_with_the_clock_are_denied(self):
        cases = [
            ('python today literal', 'tests/test_expiry.py',
             '    assert date.today() == date(2026, 11, 3)\n', 'date(2026, 11, 3'),
            ('python iso string', 'tests/test_expiry.py',
             '    assert not is_expired("2026-12-01", now=datetime.now())\n', '2026-12-01'),
            ('csharp', 'tests/ExpiryTests.cs',
             '        Assert.True(new DateTime(2027, 6, 30) > DateTime.Now);\n', 'DateTime(2027, 6, 30'),
            ('javascript zero-based month', 'src/expiry.test.ts',
             '  expect(isExpired(new Date(2026, 10, 3), Date.now())).toBe(false);\n', '2026-11-03'),
            ('go month name', 'expiry_test.go',
             '\tif time.Now().After(time.Date(2026, time.November, 30, 0, 0, 0, 0, time.UTC)) {\n', '2026-11-30'),
            ('yesterday', 'tests/test_expiry.py',
             '    assert days_since("2026/11/02", datetime.now()) == 1\n', '2026/11/02'),
        ]
        for name, path, line, expected in cases:
            with self.subTest(name=name):
                self.__new_session()
                result = self.__run_call('create', {'path': path, 'file_text': f'def test_x():\n{line}'})
                self.__assert_denied(result, [expected, f'Today is {_TODAY}', 'only once per file'], name)

    def test_edit_tools_are_parsed(self):
        cases = [
            ('edit', {'path': 'tests/test_expiry.py', 'old_str': '    pass\n',
                      'new_str': '    assert date.today() < date(2026, 12, 1)\n'}),
            ('functions.edit', json.dumps({'path': 'tests/test_expiry.py', 'old_str': '    pass',
                                           'new_str': '    assert date.today() < date(2026, 12, 1)'})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'tests/test_expiry.py', 'old_str': '    pass',
                                    'new_str': '    assert date.today() < date(2026, 12, 1)'}),
            ('insert', {'path': 'tests/test_expiry.py', 'insert_line': 4,
                        'new_str': '    assert date.today() < date(2026, 12, 1)\n'}),
            ('apply_patch', '*** Begin Patch\n*** Update File: tests/test_expiry.py\n@@\n-    pass\n'
                            '+    assert date.today() < date(2026, 12, 1)\n*** End Patch\n'),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name):
                self.__new_session()
                self.__assert_denied(self.__run_call(tool_name, args), ['tests/test_expiry.py writes date(2026, 12, 1'], tool_name)

    def test_clock_in_existing_file_is_used(self):
        self.__write_file('tests/test_clock.py', 'from datetime import datetime\nNOW = datetime.now()\n\n\ndef test_x():\n    pass\n')
        self.__assert_denied(
            self.__run_call('edit', {'path': 'tests/test_clock.py', 'old_str': '    pass', 'new_str': '    assert NOW < "2027-01-01"'}),
            ['2027-01-01', 'datetime.now('], 'A clock reference already in the file must count.',
        )

    def test_safe_writes_are_allowed(self):
        cases = [
            ('far future', 'tests/test_expiry.py', '    assert date.today() < date(2099, 1, 1)\n'),
            ('past date', 'tests/test_expiry.py', '    assert date.today() > date(2020, 1, 1)\n'),
            ('no clock', 'tests/test_expiry.py', '    assert parse("2026-11-03") == date(2026, 11, 3)\n'),
            ('frozen clock', 'tests/test_expiry.py',
             '    with freeze_time("2026-11-03"):\n        assert date.today() == date(2026, 11, 3)\n'),
            ('fake time provider', 'tests/ExpiryTests.cs',
             '        var clock = new FakeTimeProvider(new DateTime(2026, 11, 3));\n        _ = DateTime.Now;\n'),
            ('jest fake timers', 'src/expiry.test.ts',
             '  jest.useFakeTimers().setSystemTime(new Date(2026, 10, 3));\n  expect(Date.now()).toBe(1);\n'),
            ('comment', 'tests/test_expiry.py', '    # added on 2026-11-03\n    assert date.today()\n'),
            ('invalid date', 'tests/test_expiry.py', '    assert date.today() != "2026-02-30"\n'),
            ('non-test file', 'src/expiry.py', '    return date.today() > date(2026, 11, 3)\n'),
        ]
        for name, path, line in cases:
            with self.subTest(name=name):
                result = self.__run_call('create', {'path': path, 'file_text': f'def test_x():\n{line}'})
                self.__assert_allowed(result, name)
        self.assertFalse(os.path.exists(self.__state_path), 'Allowed edits must not create the state file.')

    def test_existing_date_lines_are_not_rechecked(self):
        self.__write_file('tests/test_old.py', 'def test_x():\n    assert date.today() < date(2026, 12, 1)\n    pass\n')
        self.__assert_allowed(
            self.__run_call('edit', {
                'path': 'tests/test_old.py',
                'old_str': '    assert date.today() < date(2026, 12, 1)\n    pass',
                'new_str': '    assert date.today() < date(2026, 12, 1)\n    assert True',
            }),
            'Unchanged lines must not be checked again.',
        )

    def test_same_file_is_denied_only_once(self):
        args = {'path': 'tests/test_expiry.py', 'old_str': '    pass', 'new_str': '    assert date.today() < date(2026, 12, 1)'}
        self.__assert_denied(self.__run_call('edit', args), ['date(2026, 12, 1'], 'First edit must be denied.')
        self.__assert_allowed(self.__run_call('edit', args), 'Retry must be allowed.')

    def test_other_tools_are_ignored(self):
        self.__assert_allowed(
            self.__run_call('bash', {'command': 'echo "date.today() == date(2026, 11, 3)" > tests/test_x.py'}),
            'Shell tools must be ignored.',
        )
        self.__assert_allowed(
            self.__run_call('str_replace_editor', {'command': 'view', 'path': 'tests/test_expiry.py'}),
            'view must be ignored.',
        )

    def test_invalid_input_exits_with_error(self):
        cases = [
            ('empty input', b''),
            ('invalid JSON', b'{'),
            ('non-object', b'[]'),
            ('missing sessionId', {'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}}),
            ('invalid sessionId', {'sessionId': 'abc', 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}}),
            ('missing cwd', {'sessionId': self.__session_id, 'toolName': 'edit', 'toolArgs': {}}),
            ('relative cwd', {'sessionId': self.__session_id, 'cwd': 'workspace', 'toolName': 'edit', 'toolArgs': {}}),
            ('missing toolName', {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolArgs': {}}),
            ('missing toolArgs', {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit'}),
        ]
        for name, hook_input in cases:
            with self.subTest(name=name):
                result = self.__run_hook(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check time bomb literals', result.stderr, name)

    def test_invalid_definitions_exit_with_error(self):
        valid = {'pastDays': 1, 'futureDays': 365, 'clockPatterns': ['x'], 'frozenClockPatterns': ['y']}
        cases = [
            ('not an object', []),
            ('negative days', {**valid, 'pastDays': -1}),
            ('boolean days', {**valid, 'futureDays': True}),
            ('empty clock patterns', {**valid, 'clockPatterns': []}),
            ('invalid regex', {**valid, 'frozenClockPatterns': ['(']}),
        ]
        for name, definitions in cases:
            with self.subTest(name=name):
                path = os.path.join(self.__root, 'definitions.json')
                with open(path, 'w', encoding='utf-8', newline='\n') as file:
                    json.dump(definitions, file)
                result = self.__run_hook(
                    {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}},
                    '--definitions-path', path,
                )
                self.assertEqual(result.returncode, 2, name)


if __name__ == '__main__':
    _run_tests()
