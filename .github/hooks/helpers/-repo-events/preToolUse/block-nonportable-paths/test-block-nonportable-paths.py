import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'block-nonportable-paths.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class BlockNonportablePathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-portable-paths-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = os.path.realpath(temporary.name)

    def write_file(self, path):
        full_path = os.path.join(self.directory, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as file:
            file.write('value = 1\n')

    def run_input(self, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )

    def run_call(self, tool_name, tool_args):
        return self.run_input({'cwd': self.directory, 'toolName': tool_name, 'toolArgs': tool_args})

    def run_patch(self, text):
        return self.run_call('apply_patch', '*** Begin Patch\n' + text + '*** End Patch\n')

    def assert_allowed(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_denied(self, result, *parts):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny')
        for part in parts:
            self.assertIn(part, output['permissionDecisionReason'])

    def test_configuration(self):
        with open(os.path.join(hooks_directory, '-repo-preToolUse.json'), encoding='utf-8') as file:
            entries = json.load(file)['hooks']['preToolUse']
        relative = '.github/hooks/helpers/-repo-events/preToolUse/block-nonportable-paths/block-nonportable-paths.py'
        matching = [entry for entry in entries if entry['bash'].endswith(' ' + relative)]
        self.assertEqual(len(matching), 1)
        entry = matching[0]
        self.assertEqual(entry['type'], 'command')
        self.assertEqual(entry['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative}')
        self.assertEqual(entry['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative}')
        self.assertEqual(entry['timeoutSec'], 10)
        for name in ('edit', 'functions.create', 'insert', 'str_replace_editor', 'apply_patch'):
            self.assertIsNotNone(re.fullmatch(entry['matcher'], name))
        self.assertIsNone(re.fullmatch(entry['matcher'], 'bash'))
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))
        with open(os.path.join(hooks_directory, 'preToolUse.json'), encoding='utf-8') as file:
            self.assertNotIn(relative, file.read())

    def test_reserved_names(self):
        names = [
            'CON', 'con.txt', 'PrN.log', 'AUX', 'nul.tar.gz', 'COM1', 'com9.txt', 'LPT1', 'lpt9.py',
            'CONIN$', 'CONOUT$.txt', 'CON .txt',
            *[prefix + chr(value) + '.txt' for prefix in ('COM', 'LPT') for value in (0xB9, 0xB2, 0xB3)],
        ]
        for name in names:
            with self.subTest(name=name):
                self.assert_denied(self.run_call('create', {'path': name}), 'reserved by Windows')

    def test_forbidden_characters_and_suffixes(self):
        for character in '<>:"|?*' + chr(1) + chr(31):
            with self.subTest(character=repr(character)):
                self.assert_denied(
                    self.run_call('create', {'path': 'bad' + character + 'name.txt'}), 'forbidden on Windows',
                )
        for name in ('file.', 'file ', 'folder./file.txt', 'folder /file.txt', 'NUL/file.txt'):
            with self.subTest(name=name):
                self.assert_denied(self.run_call('create', {'path': name}), 'nonportable destination')

    def test_backslash_is_forbidden_inside_a_name(self):
        helpers = runpy.run_path(hook_path)
        self.assertIn('forbidden on Windows', helpers['invalid_name_reason']('bad\\name.txt'))

    def test_portable_names(self):
        for path in ('normal.txt', 'nested/space inside.txt', '.config', 'COM0.txt', 'COM10.txt', 'LPT10', 'console.py'):
            with self.subTest(path=path):
                self.assert_allowed(self.run_call('create', {'path': path}))

    def test_existing_file_case_collision(self):
        self.write_file('Report.txt')
        self.assert_denied(self.run_call('create', {'path': 'report.txt'}), 'Report.txt', 'collides')
        self.assert_allowed(self.run_call('create', {'path': 'Report.txt'}))

    def test_existing_directory_case_collision(self):
        self.write_file('Sources/app.py')
        self.assert_denied(self.run_call('create', {'path': 'sources/new.py'}), 'Sources', 'collides')
        self.assert_allowed(self.run_call('create', {'path': 'Sources/new.py'}))

    def test_same_patch_collisions(self):
        for first, second in (
            ('Report.txt', 'report.txt'),
            ('Sources/a.py', 'sources/b.py'),
            ('Node', 'node/child.py'),
            ('caf' + chr(0xE9) + '.txt', 'cafe' + chr(0x301) + '.txt'),
        ):
            with self.subTest(first=first, second=second):
                self.assert_denied(
                    self.run_patch(f'*** Add File: {first}\n+one\n*** Add File: {second}\n+two\n'),
                    'collides with another destination',
                )

    def test_unicode_collision_with_existing_name(self):
        self.write_file('caf' + chr(0xE9) + '.txt')
        existing = os.listdir(self.directory)[0]
        alternate = 'cafe' + chr(0x301) + '.txt'
        if existing == alternate:
            alternate = 'caf' + chr(0xE9) + '.txt'
        self.assert_denied(self.run_call('create', {'path': alternate}), 'collides')
        self.write_file('Stra' + chr(0xDF) + 'e.txt')
        self.assert_denied(self.run_call('create', {'path': 'STRASSE.txt'}), 'collides')

    def test_case_only_move_is_allowed(self):
        self.write_file('Report.txt')
        self.assert_allowed(self.run_patch(
            '*** Update File: Report.txt\n*** Move to: report.txt\n@@\n-old\n+new\n',
        ))

    def test_delete_and_add_is_allowed_in_either_order(self):
        self.write_file('Report.txt')
        for text in (
            '*** Delete File: Report.txt\n*** Add File: report.txt\n+new\n',
            '*** Add File: report.txt\n+new\n*** Delete File: Report.txt\n',
        ):
            with self.subTest(text=text):
                self.assert_allowed(self.run_patch(text))

    def test_move_does_not_hide_unrelated_collision(self):
        self.write_file('one.txt')
        self.write_file('Report.txt')
        self.assert_denied(self.run_patch(
            '*** Update File: one.txt\n*** Move to: report.txt\n@@\n-old\n+new\n',
        ), 'Report.txt', 'collides')

    def test_move_to_invalid_name(self):
        self.write_file('one.txt')
        self.assert_denied(self.run_patch(
            '*** Update File: one.txt\n*** Move to: NUL.txt\n@@\n-old\n+new\n',
        ), 'reserved by Windows')

    def test_move_and_add_collision(self):
        self.write_file('one.txt')
        self.assert_denied(self.run_patch(
            '*** Update File: one.txt\n*** Move to: New.txt\n@@\n-old\n+new\n'
            '*** Add File: new.txt\n+other\n',
        ), 'collides with another destination')

    def test_moves_within_existing_directory(self):
        self.write_file('Sources/Report.txt')
        self.assert_allowed(self.run_patch(
            '*** Update File: Sources/Report.txt\n*** Move to: Sources/report.txt\n@@\n-old\n+new\n',
        ))
        self.assert_denied(self.run_patch(
            '*** Update File: Sources/Report.txt\n*** Move to: sources/report.txt\n@@\n-old\n+new\n',
        ), 'Sources', 'collides')

    def test_existing_content_update_and_delete_are_allowed(self):
        self.assert_allowed(self.run_patch('*** Update File: NUL.txt\n@@\n-old\n+new\n'))
        self.assert_allowed(self.run_patch('*** Delete File: NUL.txt\n'))
        for command in ('view', 'undo_edit'):
            self.assert_allowed(self.run_call('str_replace_editor', {'command': command, 'path': 'NUL.txt'}))
        self.assert_allowed(self.run_call('bash', {'command': 'echo untouched'}))

    def test_existing_invalid_name_is_not_rechecked(self):
        helpers = runpy.run_path(hook_path)
        # Simulate a legacy name without creating a Windows-incompatible fixture.
        with patch.object(helpers['os'], 'listdir', return_value=['NUL.txt']):
            self.assertEqual(helpers['check_paths'](self.directory, ['NUL.txt'], set()), [])

    def test_tool_formats(self):
        for tool in ('edit', 'functions.create', 'insert', 'str_replace_editor'):
            with self.subTest(tool=tool):
                self.assert_denied(
                    self.run_call(tool, json.dumps({'path': 'NUL.txt', 'command': 'create'})),
                    'reserved by Windows',
                )
        for args in (
            {'input': '*** Begin Patch\n*** Add File: CON.txt\n+new\n*** End Patch'},
            json.dumps({'patch': '*** Begin Patch\n*** Add File: CON.txt\n+new\n*** End Patch'}),
        ):
            with self.subTest(args=args):
                self.assert_denied(self.run_call('functions.apply_patch', args), 'reserved by Windows')

    def test_absolute_and_normalized_paths(self):
        self.write_file('Report.txt')
        for path in (os.path.join(self.directory, 'report.txt'), './other/../report.txt'):
            with self.subTest(path=path):
                self.assert_denied(self.run_call('create', {'path': path}), 'collides')
        self.assert_allowed(self.run_call('create', {'path': './other/../valid.txt'}))

    def test_patch_preserves_trailing_spaces_and_accepts_crlf(self):
        self.assert_denied(self.run_patch('*** Add File: bad.txt \n+new\n'), 'ends with a space')
        self.assert_denied(
            self.run_call('apply_patch', '*** Begin Patch\r\n*** Add File: CON.txt\r\n+new\r\n*** End Patch\r\n'),
            'reserved by Windows',
        )
        self.assert_allowed(self.run_patch('*** Add File: good.txt\n+*** Add File: NUL.txt\n'))

    def test_independent_destinations_are_allowed(self):
        self.assert_allowed(self.run_patch(
            '*** Add File: Sources/a.py\n+one\n*** Add File: Sources/b.py\n+two\n',
        ))

    def test_bad_input(self):
        valid = {'cwd': self.directory, 'toolName': 'create', 'toolArgs': {'path': 'valid.txt'}}
        cases = [b'', b'{', b'\xff', [], None]
        cases.extend({key: value for key, value in valid.items() if key != missing} for missing in valid)
        cases.extend([
            {**valid, 'cwd': 'relative'},
            {**valid, 'toolName': 1},
            {**valid, 'toolArgs': None},
            {**valid, 'toolArgs': {}},
            {**valid, 'toolArgs': {'path': 'bad\0name'}},
            {**valid, 'toolName': 'apply_patch', 'toolArgs': []},
        ])
        for value in cases:
            with self.subTest(value=value):
                result = self.run_input(value)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to check portable paths:', result.stderr)

    def test_filesystem_errors_are_reported(self):
        helpers = runpy.run_path(hook_path)
        with patch.object(helpers['os'], 'listdir', side_effect=PermissionError('not readable')):
            with self.assertRaises(PermissionError):
                helpers['check_paths'](self.directory, ['new.txt'], set())

    def test_repeated_invalid_operation_is_still_denied(self):
        for _ in range(2):
            self.assert_denied(self.run_call('create', {'path': 'NUL.txt'}), 'reserved by Windows')


if __name__ == '__main__':
    run_tests()
