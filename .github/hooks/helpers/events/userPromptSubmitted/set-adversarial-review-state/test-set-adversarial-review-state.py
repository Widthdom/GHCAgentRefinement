import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'set-adversarial-review-state.py')
_helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_hooks_directory = os.path.dirname(_helpers_directory)


class ReviewModeTests(unittest.TestCase):
    def setUp(self):
        self.__temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.__temporary.cleanup)
        self.__session_id = str(uuid.uuid4())
        self.__environment = os.environ.copy()
        self.__environment.update(dict.fromkeys(('TMPDIR', 'TMP', 'TEMP'), self.__temporary.name))
        self.__state_directory = os.path.join(
            self.__temporary.name, 'ghc-agent-refinement', 'hook-states', 'adversarial-review',
        )

    def __invoke(self, payload):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw, capture_output=True,
            env=self.__environment, check=False,
        )

    def __state(self, session_id=None):
        with open(os.path.join(
            self.__state_directory, f'{uuid.UUID(session_id or self.__session_id)}.json',
        ), encoding='utf-8') as file:
            return json.load(file)

    def test_prompt_suffix_and_reset(self):
        # 全角スペース（U+3000）は普通の空白と見分けられないため、コード上ではchrで書く。
        ideographic_space = chr(0x3000)
        for prompt, enabled in [
            (';;', True), ('日本語\nreview;;', True), ('regular', False),
            (' ;;review', False), ('\n;;review', False), ('x;;', True),
            (';;review', False), ('review;; ', False), ('review;;\n', False),
            ('review;;\r\n', False), ('review;;\t', False), (f'review;;{ideographic_space}', False),
            (' review;;', True), ('\nreview;;', True), ('review；；', False),
            (';', False), ('', False), ('  ', False), ('?? debug', False),
        ]:
            with self.subTest(prompt=prompt):
                result = self.__invoke({'sessionId': self.__session_id, 'prompt': prompt})
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))
                state = self.__state()
                self.assertEqual(state['enabled'], enabled)
                self.assertEqual(state['pending'], enabled)
                self.assertIsNotNone(datetime.fromisoformat(state['updatedAt']).tzinfo)
                self.assertNotIn('prompt', state)

    def test_session_isolation_normalization_and_rearming(self):
        other_id = str(uuid.uuid4())
        for session_id, prompt in [
            (self.__session_id, 'first;;'), (other_id, 'regular'),
            ('{' + self.__session_id.upper() + '}', 'again;;'),
        ]:
            self.assertEqual(self.__invoke({'sessionId': session_id, 'prompt': prompt}).returncode, 0)
        self.assertTrue(self.__state()['pending'])
        self.assertFalse(self.__state(other_id)['enabled'])
        with open(os.path.join(self.__state_directory, f'{self.__session_id}.json'), 'w', encoding='utf-8', newline='\n') as file:
            json.dump({'enabled': True, 'pending': False, 'updatedAt': 'old'}, file)
        self.assertEqual(self.__invoke({'sessionId': self.__session_id, 'prompt': 'rearm;;'}).returncode, 0)
        self.assertTrue(self.__state()['pending'])
        self.assertFalse(os.path.exists(os.path.join(
            self.__temporary.name, 'ghc-agent-refinement', 'hook-states', 'debug-mode',
        )))

    def test_subagent_does_not_overwrite_parent_mode(self):
        self.__invoke({'sessionId': self.__session_id, 'prompt': 'review;;'})
        before = self.__state()
        result = self.__invoke({
            'sessionId': self.__session_id, 'prompt': 'regular', 'agentId': 'child',
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.__state(), before)

    def test_existing_debug_hook_and_separate_state(self):
        debug_hook = os.path.join(
            _helpers_directory, 'events', 'userPromptSubmitted', 'set-debug-mode',
            'set-debug-mode.py',
        )
        debug_path = os.path.join(
            self.__temporary.name, 'ghc-agent-refinement', 'hook-states', 'debug-mode',
            f'{self.__session_id}.json',
        )
        for prompt, debug_enabled, review_enabled in (
            ('?? debug', True, False), ('review;;', False, True),
            ('?? debug and review;;', True, True), ('normal', False, False),
        ):
            with self.subTest(prompt=prompt):
                payload = {'sessionId': self.__session_id, 'prompt': prompt}
                result = subprocess.run(
                    [sys.executable, debug_hook], input=json.dumps(payload).encode('utf-8'),
                    capture_output=True, env=self.__environment, check=False,
                )
                self.assertEqual(result.returncode, 0)
                with open(debug_path, encoding='utf-8') as file:
                    before = json.load(file)
                self.assertEqual(self.__invoke(payload).returncode, 0)
                with open(debug_path, encoding='utf-8') as file:
                    self.assertEqual(json.load(file), before)
                self.assertEqual(before['enabled'], debug_enabled)
                self.assertEqual(self.__state()['enabled'], review_enabled)

    def test_invalid_input(self):
        for payload in [
            b'', b'{', b'\xff', [], None,
            {}, {'sessionId': '../escape', 'prompt': ';;'},
            {'sessionId': 123, 'prompt': ';;'},
            {'sessionId': self.__session_id}, {'sessionId': self.__session_id, 'prompt': 1},
        ]:
            with self.subTest(payload=payload):
                result = self.__invoke(payload)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertTrue(result.stderr)

    def test_write_failure_exit_code(self):
        os.makedirs(os.path.join(self.__temporary.name, 'ghc-agent-refinement', 'hook-states'))
        with open(os.path.join(self.__temporary.name, 'ghc-agent-refinement', 'hook-states', 'adversarial-review'), 'w', encoding='utf-8', newline='\n'):
            pass
        result = self.__invoke({'sessionId': self.__session_id, 'prompt': ';;'})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'Failed to save adversarial review mode:', result.stderr)


if __name__ == '__main__':
    _run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']

    _run_tests()
