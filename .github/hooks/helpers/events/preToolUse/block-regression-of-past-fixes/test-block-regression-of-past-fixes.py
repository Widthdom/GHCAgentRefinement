import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-regression-of-past-fixes.py')
_definitions_path = os.path.join(_script_directory, 'past-fix-definitions.json')
_record_hook_path = os.path.join(
    os.path.dirname(os.path.dirname(_script_directory)), 'postToolUse', 'record-viewed-commits', 'record-viewed-commits.py',
)
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_CALC_BEFORE_FIX = 'def divide(a, b):\n    return a / b\n'
_CALC_AFTER_FIX = (
    'def divide(a, b):\n'
    '    if b == 0:\n'
    "        raise ZeroDivisionError('b must not be zero')\n"
    '    return a / b\n'
)
_FIX_GUARD = "    if b == 0:\n        raise ZeroDivisionError('b must not be zero')\n"


class BlockRegressionOfPastFixesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-regression-of-past-fixes-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__environment = os.environ.copy()
        self.__environment.update({
            'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root,
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.path.join(self.__root, 'gitconfig'),
        })
        self.__directory = os.path.join(self.__root, 'workspace')
        os.makedirs(self.__directory)
        self.__git('init', '-q')
        self.__commit('src/calc.py', _CALC_BEFORE_FIX, 'Add calculator')
        self.__fix_commit_id = self.__commit('src/calc.py', _CALC_AFTER_FIX, 'fix: guard division by zero')
        self.__commit('src/round.py', 'def round_half(value):\n    return int(value + 0.5)\n', 'Add rounding')
        self.__commit(
            'src/round.py', 'import math\n\n\ndef round_half(value):\n    return math.floor(value + 0.5)\n',
            '負数の丸めのバグを修正',
        )
        self.__new_session()

    def __git(self, *arguments):
        result = subprocess.run(
            ['git', '-C', self.__directory, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
             '-c', 'commit.gpgsign=false', *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10, check=True,
        )
        return result.stdout.decode('utf-8').strip()

    def __write_file(self, path, content):
        full_path = os.path.join(self.__directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __commit(self, path, content, message):
        self.__write_file(path, content)
        self.__git('add', '--', path)
        self.__git('commit', '-q', '-m', message)
        return self.__git('rev-parse', 'HEAD')

    def __new_session(self):
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__root, 'ghc-agent-refinement', 'hook-states', 'viewed-commits', f'{self.__session_id}.sqlite3',
        )

    def __run_hook(self, hook_input, *arguments):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path, *arguments], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __run_call(self, tool_name, tool_args, cwd=None):
        return self.__run_hook({
            'sessionId': self.__session_id, 'cwd': cwd or self.__directory, 'toolName': tool_name, 'toolArgs': tool_args,
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

    def test_definitions_match_fix_commit_messages(self):
        with open(_definitions_path, encoding='utf-8') as file:
            patterns = [re.compile(pattern) for pattern in json.load(file)['fixCommitPatterns']]
        cases = [
            ('fix: guard division by zero', True),
            ('Fixes crash on empty input', True),
            ('Hotfix for login', True),
            ('bugfix: null check', True),
            ('Prevent regression in parser', True),
            ('Handle empty list (closes #123)', True),
            ('負数の丸めを修正', True),
            ('不具合対応', True),
            ('Add prefix option', False),
            ('Improve debug logging', False),
            ('Add fixture for parser tests', False),
            ('Merge pull request #13 from feature', False),
            ('Support C# 12 syntax', False),
        ]
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(any(pattern.search(message) for pattern in patterns), expected, message)

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'preToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/preToolUse/block-regression-of-past-fixes/block-regression-of-past-fixes.py'
        entries = [
            entry for entry in configuration['hooks']['preToolUse']
            if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        for tool_name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertRegex(tool_name, f'^(?:{entries[0]["matcher"]})$')
        self.assertNotRegex('bash', f'^(?:{entries[0]["matcher"]})$')

    def test_removing_fix_lines_is_denied(self):
        cases = [
            ('edit', {'path': 'src/calc.py', 'old_str': _FIX_GUARD, 'new_str': ''}),
            ('functions.edit', json.dumps({'path': 'src/calc.py', 'old_str': _FIX_GUARD, 'new_str': ''})),
            ('str_replace_editor', {'command': 'str_replace', 'path': 'src/calc.py', 'old_str': _FIX_GUARD, 'new_str': ''}),
            ('apply_patch', '*** Begin Patch\n*** Update File: src/calc.py\n@@\n def divide(a, b):\n'
                            '-    if b == 0:\n'
                            "-        raise ZeroDivisionError('b must not be zero')\n"
                            '     return a / b\n*** End Patch\n'),
            ('create', {'path': 'src/calc.py', 'file_text': _CALC_BEFORE_FIX}),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name):
                self.__new_session()
                self.__assert_denied(
                    self.__run_call(tool_name, args),
                    ['src/calc.py line 2, 3 came from', '"fix: guard division by zero"',
                     f'(run: git show {self.__fix_commit_id[:7]})', 'until every listed commit has been viewed'],
                    tool_name,
                )

    def test_rewriting_fix_lines_is_denied(self):
        self.__assert_denied(
            self.__run_call('edit', {
                'path': 'src/round.py', 'old_str': '    return math.floor(value + 0.5)', 'new_str': '    return round(value)',
            }),
            ['src/round.py line 5 came from', '"負数の丸めのバグを修正"'], 'Rewriting a fix line must be denied.',
        )

    def test_crlf_file_is_checked(self):
        self.__commit('src/crlf.py', 'def f(x):\r\n    return x or 0\r\n', 'Fix None handling')
        self.__assert_denied(
            self.__run_call('edit', {'path': 'src/crlf.py', 'old_str': '    return x or 0', 'new_str': '    return x'}),
            ['src/crlf.py line 2 came from'], 'CRLF files must be checked.',
        )

    def test_safe_edits_are_allowed(self):
        self.__write_file('src/calc.py', _CALC_AFTER_FIX + '\n\ndef draft():\n    return 1\n')
        self.__write_file('notes.py', 'value = 1\n')
        cases = [
            ('line from a non-fix commit', 'edit',
             {'path': 'src/calc.py', 'old_str': '    return a / b', 'new_str': '    return a // b'}),
            ('fix lines kept', 'edit',
             {'path': 'src/calc.py', 'old_str': _FIX_GUARD, 'new_str': '    # guard\n' + _FIX_GUARD}),
            ('insert', 'insert', {'path': 'src/calc.py', 'insert_line': 1, 'new_str': '    """Divide."""\n'}),
            ('uncommitted lines', 'edit', {'path': 'src/calc.py', 'old_str': '    return 1', 'new_str': '    return 2'}),
            ('untracked file', 'edit', {'path': 'notes.py', 'old_str': 'value = 1', 'new_str': 'value = 2'}),
            ('new file', 'create', {'path': 'src/new.py', 'file_text': 'x = 1\n'}),
            ('old text not found', 'edit', {'path': 'src/calc.py', 'old_str': 'missing', 'new_str': ''}),
        ]
        for name, tool_name, args in cases:
            with self.subTest(name=name):
                self.__assert_allowed(self.__run_call(tool_name, args), name)
        self.assertFalse(os.path.exists(self.__state_path), 'The check must not create the state file.')

    def test_outside_git_repository_is_allowed(self):
        directory = os.path.join(self.__root, 'plain')
        os.makedirs(directory)
        with open(os.path.join(directory, 'a.py'), 'w', encoding='utf-8') as file:
            file.write('if b == 0:\n    raise ValueError\n')
        self.__assert_allowed(
            self.__run_call('edit', {'path': 'a.py', 'old_str': 'if b == 0:\n    raise ValueError\n', 'new_str': ''}, directory),
            'Files outside a git repository must be allowed.',
        )

    def __view_commit(self, command):
        # 実際のgitの出力を記録側のpostToolUse hookに渡し、コミットを読んだことを記録する。
        output = self.__git(*command.split()[1:])
        result = subprocess.run(
            [sys.executable, _record_hook_path], input=json.dumps({
                'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'bash',
                'toolArgs': {'command': command}, 'toolResult': {'resultType': 'success', 'textResultForLlm': output},
            }).encode('utf-8'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def test_edit_is_denied_until_the_commit_is_viewed(self):
        args = {'path': 'src/calc.py', 'old_str': _FIX_GUARD, 'new_str': ''}
        self.__assert_denied(self.__run_call('edit', args), ['src/calc.py'], 'First edit must be denied.')
        self.__assert_denied(self.__run_call('edit', args), ['src/calc.py'], 'Retry without viewing must be denied.')
        self.__view_commit('git log --oneline')
        self.__assert_denied(self.__run_call('edit', args), ['src/calc.py'], 'A one-line list is not reading the commit.')
        self.__view_commit(f'git show {self.__fix_commit_id[:7]}')
        self.__assert_allowed(self.__run_call('edit', args), 'Edit after viewing the commit must be allowed.')

    def test_only_unviewed_commits_are_listed(self):
        self.__commit(
            'src/calc.py', _CALC_AFTER_FIX + '\n\ndef safe_mod(a, b):\n    return a % b if b else 0\n', 'Fix modulo by zero',
        )
        self.__view_commit('git log -p -n 1 -- src/calc.py')
        result = self.__run_call('create', {'path': 'src/calc.py', 'file_text': _CALC_BEFORE_FIX})
        self.__assert_denied(result, ['"fix: guard division by zero"'], 'Unviewed fix commits must still be listed.')
        self.assertNotIn(b'Fix modulo by zero', result.stdout, 'Viewed fix commits must not be listed.')

    def test_other_tools_are_ignored(self):
        self.__assert_allowed(
            self.__run_call('bash', {'command': "sed -i '/b == 0/d' src/calc.py"}), 'Shell tools must be ignored.',
        )
        self.__assert_allowed(
            self.__run_call('str_replace_editor', {'command': 'view', 'path': 'src/calc.py'}), 'view must be ignored.',
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
                self.assertIn(b'Failed to check regression of past fixes', result.stderr, name)

    def test_invalid_definitions_exit_with_error(self):
        cases = [
            ('not an object', []),
            ('missing patterns', {}),
            ('empty patterns', {'fixCommitPatterns': []}),
            ('blank pattern', {'fixCommitPatterns': [' ']}),
            ('invalid regex', {'fixCommitPatterns': ['(']}),
        ]
        for name, definitions in cases:
            with self.subTest(name=name):
                path = os.path.join(self.__root, 'definitions.json')
                with open(path, 'w', encoding='utf-8') as file:
                    json.dump(definitions, file)
                result = self.__run_hook(
                    {'sessionId': self.__session_id, 'cwd': self.__directory, 'toolName': 'edit', 'toolArgs': {}},
                    '--definitions-path', path,
                )
                self.assertEqual(result.returncode, 2, name)


if __name__ == '__main__':
    run_tests()
