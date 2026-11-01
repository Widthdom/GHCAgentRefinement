import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'record-hook-change-requests.py')
hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory))))
sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class RecordHookChangeRequestsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-hook-change-requests-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.repository = os.path.join(self.root, 'repository')
        self.cwd = os.path.join(self.repository, 'src')
        os.makedirs(self.cwd)
        for events_directory, event, name in (
            ('events', 'preToolUse', 'block-commands'),
            ('-repo-events', 'preToolUse', 'block-native-path-separators'),
        ):
            os.makedirs(os.path.join(self.repository, '.github', 'hooks', 'helpers', events_directory, event, name))
        with open(os.path.join(self.repository, '.github', 'hooks', 'helpers', 'events', 'preToolUse', 'README.md'), 'w') as file:
            file.write('not a hook directory')
        self.session_id = str(uuid.uuid4())
        self.state_path = os.path.join(
            self.root, 'ghc-agent-refinement', 'hook-states', 'hook-change-requests', f'{self.session_id}.json',
        )

    def run_process(self, hook_input):
        raw = hook_input if isinstance(hook_input, bytes) else json.dumps(hook_input).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=10,
        )

    def submit(self, prompt, **extra):
        return self.run_process({
            'sessionId': self.session_id, 'timestamp': 0, 'cwd': self.cwd, 'prompt': prompt, **extra,
        })

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def read_state(self):
        with open(self.state_path, encoding='utf-8') as file:
            return json.load(file)

    def test_configuration(self):
        with open(os.path.join(hooks_directory, 'userPromptSubmitted.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/userPromptSubmitted/record-hook-change-requests/record-hook-change-requests.py'
        entries = configuration['hooks']['userPromptSubmitted']
        matching = [entry for entry in entries if relative_path in entry['bash']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['bash'], f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual([entry['bash'] for entry in entries], sorted(entry['bash'] for entry in entries))

    def test_prompts_mentioning_hooks_are_recorded(self):
        prompts = (
            'hookを新規作成して',
            'フックを直して',
            'Update the pre-commit Hook',
            '.github/hooks/preToolUse.json の順序を直して',
            'preToolUseの拒否理由を変えて',
            'agentStop の判定を緩めて',
            'Block-Commands の定義を追加して',
            'block-native-path-separatorsのREADMEを直して',
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                if os.path.exists(self.state_path):
                    os.remove(self.state_path)
                self.assert_silent(self.submit(prompt))
                state = self.read_state()
                self.assertIs(state['permitted'], True)
                self.assertIsInstance(state['updatedAt'], str)
                self.assertNotIn(prompt, json.dumps(state, ensure_ascii=False))

    def test_other_prompts_are_not_recorded(self):
        for prompt in ('READMEを直して', 'テストを全部実行して', '1,2,3の順に対応して', ''):
            with self.subTest(prompt=prompt):
                self.assert_silent(self.submit(prompt))
                self.assertFalse(os.path.exists(self.state_path))

    def test_permission_persists_for_the_session(self):
        self.assert_silent(self.submit('hookを直して'))
        self.assert_silent(self.submit('続けて'))
        self.assertIs(self.read_state()['permitted'], True)

    def test_subagent_prompts_are_ignored(self):
        self.assert_silent(self.submit('hookのファイルを削除して', agentId='child-agent'))
        self.assertFalse(os.path.exists(self.state_path))

    def test_works_without_hooks_directory(self):
        outside = os.path.join(self.root, 'outside')
        os.makedirs(outside)
        self.assert_silent(self.run_process({'sessionId': self.session_id, 'cwd': outside, 'prompt': 'block-commandsを直して'}))
        self.assertFalse(os.path.exists(self.state_path))
        self.assert_silent(self.run_process({'sessionId': self.session_id, 'cwd': outside, 'prompt': 'hookを直して'}))
        self.assertIs(self.read_state()['permitted'], True)

    def test_invalid_input(self):
        def encode(value):
            return json.dumps(value).encode('utf-8')

        for name, hook_input in [
            ('empty', b''),
            ('not json', b'{'),
            ('not object', b'[]'),
            ('invalid utf-8', b'\xff'),
            ('no prompt', encode({'sessionId': self.session_id, 'cwd': self.cwd})),
            ('prompt not string', encode({'sessionId': self.session_id, 'cwd': self.cwd, 'prompt': 1})),
            ('no session', encode({'cwd': self.cwd, 'prompt': 'hook'})),
            ('invalid session', encode({'sessionId': 'x', 'cwd': self.cwd, 'prompt': 'hook'})),
            ('no cwd', encode({'sessionId': self.session_id, 'prompt': 'hook'})),
            ('relative cwd', encode({'sessionId': self.session_id, 'cwd': 'relative', 'prompt': 'hook'})),
        ]:
            with self.subTest(name=name):
                result = self.run_process(hook_input)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to record hook change request:', result.stderr)
        self.assertFalse(os.path.exists(self.state_path))


if __name__ == '__main__':
    run_tests()
