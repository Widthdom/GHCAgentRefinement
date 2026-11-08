import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-secret-writes.py')
_definitions_path = os.path.join(_script_directory, 'secret-definitions.json')

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

# このテスト自体が本hookに拒否されないよう、秘密情報に見える値は接頭辞と本体を連結して作る。
_BODY = 'Ab3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF3hJ5'
_SECRETS = {
    'Anthropic API key': 'sk-' + 'ant-api03-' + _BODY,
    'OpenAI API key': 'sk-' + 'proj-' + _BODY,
    'GitHub token': 'gh' + 'p_' + _BODY + 'xy',
    'AWS access key ID': 'AK' + 'IA' + 'ABCDEFGHIJ234567',
    'Google API key': 'AI' + 'za' + _BODY[:35],
    'Slack token': 'xo' + 'xb-' + '1234567890-abcdefghij',
    'Stripe live key': 'sk' + '_live_' + _BODY[:24],
    'Private key': '-----BEGIN ' + 'RSA PRIVATE KEY-----',
}


def _run_hook(input_bytes, *arguments):
    return subprocess.run([sys.executable, _hook_path, *arguments], input=input_bytes, capture_output=True, timeout=30)


class BlockSecretWritesTests(unittest.TestCase):
    def setUp(self):
        self.__directory = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.__directory, ignore_errors=True)

    def __run_call(self, tool_call):
        hook_input = {'sessionId': '00000000-0000-4000-8000-000000000000', 'cwd': self.__directory, 'toolName': tool_call['name'], 'toolArgs': tool_call['args']}
        return _run_hook(json.dumps(hook_input).encode('utf-8'))

    def __write_file(self, name, content):
        path = os.path.join(self.__directory, name)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)
        return path

    def __assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def __assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)
        return output['permissionDecisionReason']

    def test_definitions_are_valid(self):
        with open(_definitions_path, encoding='utf-8') as file:
            definitions = json.load(file)
        self.assertEqual([definition['name'] for definition in definitions], list(_SECRETS))

    def test_each_secret_is_denied(self):
        for name, secret in _SECRETS.items():
            with self.subTest(name=name):
                reason = self.__assert_denied(
                    self.__run_call({'name': 'create', 'args': {'path': 'config.py', 'file_text': f'KEY = "{secret}"\n'}}),
                    ['config.py', f'({name})'], name,
                )
                self.assertNotIn(secret, reason, 'The reason must not repeat the secret.')

    def test_tool_argument_shapes(self):
        secret = _SECRETS['GitHub token']
        cases = [
            ('edit', {'path': 'a.txt', 'old_str': 'x', 'new_str': secret}),
            ('functions.edit', {'path': 'a.txt', 'old_str': 'x', 'new_str': secret}),
            ('insert', {'path': 'a.txt', 'insert_line': 1, 'new_str': secret}),
            ('str_replace_editor', {'command': 'create', 'path': 'a.txt', 'file_text': secret}),
            ('edit', json.dumps({'path': 'a.txt', 'old_str': 'x', 'new_str': secret})),
            ('apply_patch', f'*** Begin Patch\n*** Add File: a.txt\n+{secret}\n*** End Patch\n'),
        ]
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name, args=args):
                self.__assert_denied(self.__run_call({'name': tool_name, 'args': args}), ['(GitHub token)'], tool_name)

    def test_safe_content_is_allowed(self):
        cases = [
            ('plain code', {'path': 'a.py', 'file_text': 'print("hello")\n'}),
            ('environment variable', {'path': 'a.py', 'file_text': 'key = os.environ["ANTHROPIC_API_KEY"]\n'}),
            ('short prefix', {'path': 'a.md', 'file_text': 'Keys start with `sk-ant-` or `ghp_`.\n'}),
            ('placeholder', {'path': 'a.md', 'file_text': 'gh' + 'p_' + 'x' * 36 + '\n'}),
            ('zero placeholder', {'path': 'a.md', 'file_text': 'AK' + 'IA' + '0' * 16 + '\n'}),
            ('public key', {'path': 'a.pub', 'file_text': '-----BEGIN PUBLIC KEY-----\n'}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                self.__assert_allowed(self.__run_call({'name': 'create', 'args': args}), name)

    def test_existing_secret_is_allowed(self):
        secret = _SECRETS['Slack token']
        self.__assert_allowed(
            self.__run_call({'name': 'edit', 'args': {'path': 'a.txt', 'old_str': f'a {secret}', 'new_str': f'b {secret}'}}),
            'Moving a secret that is already in the replaced text must be allowed.',
        )
        self.__write_file('b.txt', f'token = {secret}\n')
        self.__assert_allowed(
            self.__run_call({'name': 'insert', 'args': {'path': 'b.txt', 'insert_line': 1, 'new_str': f'# {secret}'}}),
            'A secret that the file already contains must be allowed.',
        )
        patch = f'*** Begin Patch\n*** Update File: c.txt\n-old {secret}\n+new {secret}\n*** End Patch\n'
        self.__assert_allowed(
            self.__run_call({'name': 'apply_patch', 'args': patch}),
            'A secret on a removed patch line must be allowed.',
        )

    def test_view_and_other_tools_are_ignored(self):
        secret = _SECRETS['Anthropic API key']
        for tool_call in (
            {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': '.env'}},
            {'name': 'bash', 'args': {'command': f'echo {secret}'}},
            {'name': 'view', 'args': {'path': '.env'}},
        ):
            with self.subTest(tool_call=tool_call):
                self.__assert_allowed(self.__run_call(tool_call), 'Non-writing tool calls must be ignored.')

    def test_env_files(self):
        for name in ('.env', '.env.local', '.ENV.production', os.path.join('app', '.env')):
            with self.subTest(name=name):
                self.__assert_denied(
                    self.__run_call({'name': 'create', 'args': {'path': name, 'file_text': 'DEBUG=1\n'}}),
                    ['is an environment file'], name,
                )
        patch = '*** Begin Patch\n*** Update File: config.txt\n*** Move to: .env\n*** End Patch\n'
        self.__assert_denied(self.__run_call({'name': 'apply_patch', 'args': patch}), ['.env is an environment file'], 'move')
        for name in ('.env.example', '.env.sample', '.env.template', '.env.dist', 'env.py', '.environment'):
            with self.subTest(name=name):
                self.__assert_allowed(
                    self.__run_call({'name': 'create', 'args': {'path': name, 'file_text': 'API_KEY=\n'}}), name,
                )

    def test_multiple_findings(self):
        patch_text = (
            '*** Begin Patch\n*** Add File: a.txt\n'
            + ''.join(f'+{line}\n' for line in _SECRETS['AWS access key ID'].splitlines())
            + '*** Add File: b.txt\n'
            + ''.join(f'+{line}\n' for line in _SECRETS['Private key'].splitlines())
            + '*** End Patch\n'
        )
        reason = self.__assert_denied(
            self.__run_call({'name': 'apply_patch', 'args': patch_text}),
            ['a.txt, b.txt', '(AWS access key ID, Private key)'], 'Patch writes',
        )
        self.assertNotIn('OpenAI', reason)

    def test_anthropic_key_is_not_reported_as_openai(self):
        reason = self.__assert_denied(
            self.__run_call({'name': 'create', 'args': {'path': 'a.txt', 'file_text': _SECRETS['Anthropic API key']}}),
            ['(Anthropic API key)'], 'Anthropic key',
        )
        self.assertNotIn('OpenAI', reason)

    def test_invalid_input(self):
        for name, hook_input in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('no cwd', json.dumps({'toolName': 'create', 'toolArgs': {}}).encode('utf-8')),
            ('relative cwd', json.dumps({'cwd': 'relative', 'toolName': 'create', 'toolArgs': {}}).encode('utf-8')),
            ('no tool name', json.dumps({'cwd': self.__directory, 'toolArgs': {}}).encode('utf-8')),
            ('empty tool name', json.dumps({'cwd': self.__directory, 'toolName': ' ', 'toolArgs': {}}).encode('utf-8')),
            ('tool name not string', json.dumps({'cwd': self.__directory, 'toolName': 1, 'toolArgs': {}}).encode('utf-8')),
            ('no tool args', json.dumps({'cwd': self.__directory, 'toolName': 'create'}).encode('utf-8')),
            ('invalid utf-8', b'\xff'),
        ]:
            with self.subTest(name=name):
                result = _run_hook(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check secret writes:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = json.dumps({'cwd': self.__directory, 'toolName': 'create', 'toolArgs': {}}).encode('utf-8')
        for name, content in [
            ('not json', '{'),
            ('not array', '{}'),
            ('empty array', '[]'),
            ('missing pattern', '[{"name": "x"}]'),
            ('invalid regex', '[{"name": "x", "pattern": "("}]'),
        ]:
            with self.subTest(name=name):
                path = self.__write_file(f'definitions-{name.replace(" ", "-")}.json', content)
                result = _run_hook(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check secret writes:', result.stderr)
        result = _run_hook(valid_input, '--definitions-path', os.path.join(self.__directory, 'missing.json'))
        self.assertEqual(result.returncode, 2, 'A missing definitions file must fail.')


if __name__ == '__main__':
    _run_tests()
