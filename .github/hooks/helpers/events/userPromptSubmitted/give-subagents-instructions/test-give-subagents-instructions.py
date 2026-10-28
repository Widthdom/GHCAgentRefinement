import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_name = 'give-subagents-instructions'
hook_path = os.path.join(script_directory, f'{hook_name}.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
real_instructions_path = os.path.join(os.path.dirname(hooks_directory), 'copilot-instructions.md')
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class GiveSubagentsInstructionsTests(unittest.TestCase):
    def setUp(self):
        self.subagent_payload = {
            'sessionId': '80cbbcbb-550a-458e-b40e-6734f4c890d8',
            'agentId': 'ff218530-a3e9-491e-a532-25b960bf6e9b',
            'agentType': 'explore',
            'parentAgentId': '80cbbcbb-550a-458e-b40e-6734f4c890d8',
            'prompt': '既存hookの構成を調査して報告して',
            'timestamp': 0,
            'cwd': os.path.dirname(os.path.dirname(hooks_directory)),
        }
        self.parent_payload = {key: value for key, value in self.subagent_payload.items() if key not in (
            'agentId', 'agentType', 'parentAgentId',
        )}

    def new_sandbox(self):
        # 指示ファイルを差し替えるため、.githubからの相対位置を保ってスクリプトを一時ディレクトリへ複製する。
        root = tempfile.mkdtemp(prefix='ghc-give-subagents-instructions-test-')
        self.addCleanup(shutil.rmtree, root)
        directory = os.path.join(root, '.github', 'hooks', 'helpers', 'events', 'userPromptSubmitted', hook_name)
        os.makedirs(directory)
        shutil.copy(hook_path, directory)
        return os.path.join(directory, f'{hook_name}.py'), os.path.join(root, '.github', 'copilot-instructions.md')

    @staticmethod
    def invoke(script_path, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, script_path], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )

    def assert_context(self, result, expected):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        self.assertRegex(result.stdout.decode('utf-8'), r'^[^\r\n]+\r?\n$')
        self.assertEqual(json.loads(result.stdout.decode('utf-8')), {'additionalContext': expected})

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'userPromptSubmitted.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = f'.github/hooks/helpers/events/userPromptSubmitted/{hook_name}/{hook_name}.py'
        entries = [
            entry for entry in configuration['hooks']['userPromptSubmitted']
            if entry.get('bash') == f'python3 {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python {relative_path}')

    def test_passes_real_instructions_to_subagent(self):
        with open(real_instructions_path, 'rb') as file:
            expected = file.read().decode('utf-8-sig')
        self.assert_context(self.invoke(hook_path, self.subagent_payload), expected)

    def test_preserves_instructions_content(self):
        cases = (
            ('# 指示\n- 応答は日本語を用いる。\n', None),
            ('line1\r\nline2\r\n', None),
            (' \t\n指示 😀\r\n \t', None),
            ("quote \" backslash \\ <tag> & 'single'", None),
            ('# 指示\n', b'\xef\xbb\xbf' + '# 指示\n'.encode('utf-8')),
        )
        for content, data in cases:
            with self.subTest(content=content):
                script_path, instructions_path = self.new_sandbox()
                with open(instructions_path, 'wb') as file:
                    file.write(data if data is not None else content.encode('utf-8'))
                self.assert_context(self.invoke(script_path, self.subagent_payload), content)

    def test_skips_parent_prompt(self):
        # 親への依頼では、指示ファイルがなくても何もしない。
        script_path, _ = self.new_sandbox()
        for payload in (self.parent_payload, {**self.parent_payload, 'agentId': None}):
            with self.subTest(payload=payload):
                result = self.invoke(script_path, payload)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def test_invalid_instructions_file_reports_error_for_subagent(self):
        cases = (
            (None, 'Copilot instructions file not found'),
            (b'', 'Copilot instructions file is empty'),
            (b' \t\r\n', 'Copilot instructions file is empty'),
            (b'\xef\xbb\xbf', 'Copilot instructions file is empty'),
        )
        for data, error in cases:
            with self.subTest(data=data):
                script_path, instructions_path = self.new_sandbox()
                if data is not None:
                    with open(instructions_path, 'wb') as file:
                        file.write(data)
                result = self.invoke(script_path, self.subagent_payload)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(error, result.stderr.decode('utf-8'))

    def test_invalid_input_reports_error(self):
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'true', b'{"agentId":"\xff"}']
        for value in ('', ' \t', 3, False, [], {}):
            invalid.append(json.dumps({**self.subagent_payload, 'agentId': value}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.invoke(hook_path, raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to parse userPromptSubmitted hook input:', result.stderr)


if __name__ == '__main__':
    run_tests()
