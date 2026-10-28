import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from unittest.mock import patch


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'set-adversarial-review-state.py')
helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
hooks_directory = os.path.dirname(helpers_directory)


class ReviewModeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.session_id = str(uuid.uuid4())
        self.environment = os.environ.copy()
        self.environment.update(dict.fromkeys(('TMPDIR', 'TMP', 'TEMP'), self.temporary.name))
        self.state_directory = os.path.join(
            self.temporary.name, 'ghc-agent-refinement', 'hook-states', 'adversarial-review',
        )

    def invoke(self, payload):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
        return subprocess.run(
            [sys.executable, hook_path], input=raw, capture_output=True,
            env=self.environment, check=False,
        )

    def state(self, session_id=None):
        with open(os.path.join(
            self.state_directory, f'{uuid.UUID(session_id or self.session_id)}.json',
        ), encoding='utf-8') as file:
            return json.load(file)

    def test_prompt_suffix_and_reset(self):
        for prompt, enabled in [
            (';;', True), ('日本語\nreview;;', True), ('regular', False),
            (' ;;review', False), ('\n;;review', False), ('x;;', True),
            (';;review', False), ('review;; ', False), ('review;;\n', False),
            ('review;;\r\n', False), ('review;;\t', False), ('review;;　', False),
            (' review;;', True), ('\nreview;;', True), ('review；；', False),
            (';', False), ('', False), ('  ', False), ('?? debug', False),
        ]:
            with self.subTest(prompt=prompt):
                result = self.invoke({'sessionId': self.session_id, 'prompt': prompt})
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))
                state = self.state()
                self.assertEqual(state['enabled'], enabled)
                self.assertEqual(state['pending'], enabled)
                self.assertIsNotNone(datetime.fromisoformat(state['updatedAt']).tzinfo)
                self.assertNotIn('prompt', state)

    def test_session_isolation_normalization_and_rearming(self):
        other_id = str(uuid.uuid4())
        for session_id, prompt in [
            (self.session_id, 'first;;'), (other_id, 'regular'),
            ('{' + self.session_id.upper() + '}', 'again;;'),
        ]:
            self.assertEqual(self.invoke({'sessionId': session_id, 'prompt': prompt}).returncode, 0)
        self.assertTrue(self.state()['pending'])
        self.assertFalse(self.state(other_id)['enabled'])
        with open(os.path.join(self.state_directory, f'{self.session_id}.json'), 'w') as file:
            json.dump({'enabled': True, 'pending': False, 'updatedAt': 'old'}, file)
        self.assertEqual(self.invoke({'sessionId': self.session_id, 'prompt': 'rearm;;'}).returncode, 0)
        self.assertTrue(self.state()['pending'])
        self.assertFalse(os.path.exists(os.path.join(
            self.temporary.name, 'ghc-agent-refinement', 'hook-states', 'debug-mode',
        )))

    def test_subagent_does_not_overwrite_parent_mode(self):
        self.invoke({'sessionId': self.session_id, 'prompt': 'review;;'})
        before = self.state()
        result = self.invoke({
            'sessionId': self.session_id, 'prompt': 'regular', 'agentId': 'child',
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.state(), before)

    def test_existing_debug_hook_and_separate_state(self):
        debug_hook = os.path.join(
            helpers_directory, 'events', 'userPromptSubmitted', 'set-debug-mode',
            'set-debug-mode.py',
        )
        debug_path = os.path.join(
            self.temporary.name, 'ghc-agent-refinement', 'hook-states', 'debug-mode',
            f'{self.session_id}.json',
        )
        for prompt, debug_enabled, review_enabled in (
            ('?? debug', True, False), ('review;;', False, True),
            ('?? debug and review;;', True, True), ('normal', False, False),
        ):
            with self.subTest(prompt=prompt):
                payload = {'sessionId': self.session_id, 'prompt': prompt}
                result = subprocess.run(
                    [sys.executable, debug_hook], input=json.dumps(payload).encode('utf-8'),
                    capture_output=True, env=self.environment, check=False,
                )
                self.assertEqual(result.returncode, 0)
                with open(debug_path, encoding='utf-8') as file:
                    before = json.load(file)
                self.assertEqual(self.invoke(payload).returncode, 0)
                with open(debug_path, encoding='utf-8') as file:
                    self.assertEqual(json.load(file), before)
                self.assertEqual(before['enabled'], debug_enabled)
                self.assertEqual(self.state()['enabled'], review_enabled)

    def test_invalid_input(self):
        for payload in [
            b'', b'{', b'\xff', [], None,
            {}, {'sessionId': '../escape', 'prompt': ';;'},
            {'sessionId': 123, 'prompt': ';;'},
            {'sessionId': self.session_id}, {'sessionId': self.session_id, 'prompt': 1},
        ]:
            with self.subTest(payload=payload):
                result = self.invoke(payload)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertTrue(result.stderr)

    def test_write_failure_exit_code(self):
        os.makedirs(os.path.join(self.temporary.name, 'ghc-agent-refinement', 'hook-states'))
        with open(os.path.join(self.temporary.name, 'ghc-agent-refinement', 'hook-states', 'adversarial-review'), 'w'):
            pass
        result = self.invoke({'sessionId': self.session_id, 'prompt': ';;'})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to save adversarial review mode:', result.stderr)

    def test_configuration_preserves_existing_hook(self):
        with open(os.path.join(hooks_directory, 'userPromptSubmitted.json'), encoding='utf-8') as file:
            commands = json.load(file)['hooks']['userPromptSubmitted']
        for key, interpreter in (('bash', 'python3'), ('powershell', 'python')):
            for hook_name in ('set-debug-mode', 'set-adversarial-review-state'):
                with self.subTest(key=key, hook_name=hook_name):
                    self.assertIn(
                        f'{interpreter} .github/hooks/helpers/events/userPromptSubmitted/'
                        f'{hook_name}/{hook_name}.py',
                        [command[key] for command in commands],
                    )


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(hooks_directory, 'tests', 'helpers'))
    from hook_test_output import run_tests

    run_tests()
