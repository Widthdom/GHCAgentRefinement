import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'block-secret-writes.py')
definitions_path = os.path.join(script_directory, 'secret-definitions.json')

hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

# このテスト自体が本hookに拒否されないよう、秘密情報に見える値は接頭辞と本体を連結して作る。
BODY = 'Ab3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF3hJ5'
SECRETS = {
    'Anthropic API key': 'sk-' + 'ant-api03-' + BODY,
    'OpenAI API key': 'sk-' + 'proj-' + BODY,
    'GitHub token': 'gh' + 'p_' + BODY + 'xy',
    'AWS access key ID': 'AK' + 'IA' + 'ABCDEFGHIJ234567',
    'Google API key': 'AI' + 'za' + BODY[:35],
    'Slack token': 'xo' + 'xb-' + '1234567890-abcdefghij',
    'Stripe live key': 'sk' + '_live_' + BODY[:24],
    'Private key': '-----BEGIN ' + 'RSA PRIVATE KEY-----',
}


def run_hook(input_bytes, *arguments):
    return subprocess.run([sys.executable, hook_path, *arguments], input=input_bytes, capture_output=True)


class BlockSecretWritesTests(unittest.TestCase):
    def setUp(self):
        self.directory = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def run_calls(self, *tool_calls):
        hook_input = {'sessionId': '00000000-0000-4000-8000-000000000000', 'cwd': self.directory, 'toolCalls': list(tool_calls)}
        return run_hook(json.dumps(hook_input).encode('utf-8'))

    def write_file(self, name, content):
        path = os.path.join(self.directory, name)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)
        return path

    def assert_allowed(self, result, message):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''), message)

    def assert_denied(self, result, expected_parts, message):
        self.assertEqual(result.returncode, 0, f'{message} {result.stderr!r}')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'), f'{message} stdout must not start with a BOM.')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['permissionDecision'], 'deny', message)
        for part in expected_parts:
            self.assertIn(part, output['permissionDecisionReason'], message)
        return output['permissionDecisionReason']

    def test_definitions_are_valid(self):
        with open(definitions_path, encoding='utf-8') as file:
            definitions = json.load(file)
        self.assertEqual([definition['name'] for definition in definitions], list(SECRETS))

    def test_each_secret_is_denied(self):
        for name, secret in SECRETS.items():
            with self.subTest(name=name):
                reason = self.assert_denied(
                    self.run_calls({'name': 'create', 'args': {'path': 'config.py', 'file_text': f'KEY = "{secret}"\n'}}),
                    ['config.py', f'({name})'], name,
                )
                self.assertNotIn(secret, reason, 'The reason must not repeat the secret.')

    def test_tool_argument_shapes(self):
        secret = SECRETS['GitHub token']
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
                self.assert_denied(self.run_calls({'name': tool_name, 'args': args}), ['(GitHub token)'], tool_name)

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
                self.assert_allowed(self.run_calls({'name': 'create', 'args': args}), name)

    def test_existing_secret_is_allowed(self):
        secret = SECRETS['Slack token']
        self.assert_allowed(
            self.run_calls({'name': 'edit', 'args': {'path': 'a.txt', 'old_str': f'a {secret}', 'new_str': f'b {secret}'}}),
            'Moving a secret that is already in the replaced text must be allowed.',
        )
        self.write_file('b.txt', f'token = {secret}\n')
        self.assert_allowed(
            self.run_calls({'name': 'insert', 'args': {'path': 'b.txt', 'insert_line': 1, 'new_str': f'# {secret}'}}),
            'A secret that the file already contains must be allowed.',
        )
        patch = f'*** Begin Patch\n*** Update File: c.txt\n-old {secret}\n+new {secret}\n*** End Patch\n'
        self.assert_allowed(
            self.run_calls({'name': 'apply_patch', 'args': patch}),
            'A secret on a removed patch line must be allowed.',
        )

    def test_view_and_other_tools_are_ignored(self):
        secret = SECRETS['Anthropic API key']
        self.assert_allowed(
            self.run_calls(
                {'name': 'str_replace_editor', 'args': {'command': 'view', 'path': '.env'}},
                {'name': 'bash', 'args': {'command': f'echo {secret}'}},
                {'name': 'view', 'args': {'path': '.env'}},
            ),
            'Non-writing tool calls must be ignored.',
        )

    def test_env_files(self):
        for name in ('.env', '.env.local', '.ENV.production', os.path.join('app', '.env')):
            with self.subTest(name=name):
                self.assert_denied(
                    self.run_calls({'name': 'create', 'args': {'path': name, 'file_text': 'DEBUG=1\n'}}),
                    ['is an environment file'], name,
                )
        patch = '*** Begin Patch\n*** Update File: config.txt\n*** Move to: .env\n*** End Patch\n'
        self.assert_denied(self.run_calls({'name': 'apply_patch', 'args': patch}), ['.env is an environment file'], 'move')
        for name in ('.env.example', '.env.sample', '.env.template', '.env.dist', 'env.py', '.environment'):
            with self.subTest(name=name):
                self.assert_allowed(
                    self.run_calls({'name': 'create', 'args': {'path': name, 'file_text': 'API_KEY=\n'}}), name,
                )

    def test_multiple_findings(self):
        reason = self.assert_denied(
            self.run_calls(
                {'name': 'create', 'args': {'path': 'a.txt', 'file_text': SECRETS['AWS access key ID']}},
                {'name': 'create', 'args': {'path': 'b.txt', 'file_text': SECRETS['Private key']}},
            ),
            ['a.txt', '(AWS access key ID)', 'b.txt', '(Private key)'], 'Parallel writes',
        )
        self.assertNotIn('OpenAI', reason)

    def test_anthropic_key_is_not_reported_as_openai(self):
        reason = self.assert_denied(
            self.run_calls({'name': 'create', 'args': {'path': 'a.txt', 'file_text': SECRETS['Anthropic API key']}}),
            ['(Anthropic API key)'], 'Anthropic key',
        )
        self.assertNotIn('OpenAI', reason)

    def test_invalid_input(self):
        for name, hook_input in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('no cwd', json.dumps({'toolCalls': [{'name': 'create', 'args': {}}]}).encode('utf-8')),
            ('relative cwd', json.dumps({'cwd': 'relative', 'toolCalls': [{'name': 'create', 'args': {}}]}).encode('utf-8')),
            ('no tool calls', json.dumps({'cwd': self.directory}).encode('utf-8')),
            ('empty tool calls', json.dumps({'cwd': self.directory, 'toolCalls': []}).encode('utf-8')),
            ('tool call not object', json.dumps({'cwd': self.directory, 'toolCalls': ['create']}).encode('utf-8')),
            ('no tool name', json.dumps({'cwd': self.directory, 'toolCalls': [{'args': {}}]}).encode('utf-8')),
            ('invalid utf-8', b'\xff'),
        ]:
            with self.subTest(name=name):
                result = run_hook(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check secret writes:', result.stderr)

    def test_invalid_definitions(self):
        valid_input = json.dumps({'cwd': self.directory, 'toolCalls': [{'name': 'create', 'args': {}}]}).encode('utf-8')
        for name, content in [
            ('not json', '{'),
            ('not array', '{}'),
            ('empty array', '[]'),
            ('missing pattern', '[{"name": "x"}]'),
            ('invalid regex', '[{"name": "x", "pattern": "("}]'),
        ]:
            with self.subTest(name=name):
                path = self.write_file(f'definitions-{name.replace(" ", "-")}.json', content)
                result = run_hook(valid_input, '--definitions-path', path)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(b'Failed to check secret writes:', result.stderr)
        result = run_hook(valid_input, '--definitions-path', os.path.join(self.directory, 'missing.json'))
        self.assertEqual(result.returncode, 2, 'A missing definitions file must fail.')


if __name__ == '__main__':
    run_tests()
