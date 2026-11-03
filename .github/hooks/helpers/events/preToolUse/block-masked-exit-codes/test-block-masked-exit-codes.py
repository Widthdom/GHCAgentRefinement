import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'block-masked-exit-codes.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests


class BlockMaskedExitCodesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-block-masked-exit-codes-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = temporary.name
        self.__session_id = '00000000-0000-4000-8000-000000000000'
        self.__environment = os.environ.copy()
        self.__environment.update({'TMPDIR': self.__root, 'TEMP': self.__root, 'TMP': self.__root})

    def __payload(self, tool_call, session_id=None):
        return {
            'sessionId': self.__session_id if session_id is None else session_id,
            'cwd': self.__root,
            'toolName': tool_call[0],
            'toolArgs': tool_call[1],
        }

    def __run_hook(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.__environment, timeout=10,
        )

    def __invoke(self, command, tool_name='bash', session_id=None):
        return self.__run_hook(self.__payload((tool_name, {'command': command}), session_id=session_id))

    def __assert_allowed(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_denied(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'permissionDecision', 'permissionDecisionReason'})
        self.assertEqual(output['permissionDecision'], 'deny')
        self.assertIn('set -o pipefail', output['permissionDecisionReason'])
        for fragment in fragments:
            self.assertIn(fragment, output['permissionDecisionReason'])
        return output['permissionDecisionReason']

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'preToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        entries = configuration['hooks']['preToolUse']
        relative_path = '.github/hooks/helpers/events/preToolUse/block-masked-exit-codes/block-masked-exit-codes.py'
        matching = [
            entry for entry in entries
            if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['type'], 'command')
        self.assertEqual(matching[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        for tool_name in ('bash', 'functions.bash'):
            self.assertRegex(tool_name, f'^(?:{matching[0]["matcher"]})$')
        self.assertNotRegex('edit', f'^(?:{matching[0]["matcher"]})$')

    def test_masked_checks_are_denied(self):
        for command, fragment in [
            ('dotnet test | tail -20', '"dotnet test"'),
            ('python3 -m pytest -q 2>&1 | grep -E "FAILED|passed"', '"python3 -m pytest -q"'),
            ('python3 .github/hooks/tests/test-all-hooks.py | tail -5', 'test-all-hooks.py'),
            ('make all |& tee build.log', '"make all"'),
            ('npm run build | head -50', '"npm run build"'),
            ('cd src && CI=1 timeout 600 cargo test 2>&1 | tail', '"CI=1 timeout 600 cargo test"'),
            ('git status\ndotnet build | grep error', '"dotnet build"'),
            ('pytest | tee log.txt | grep -c FAILED', '"pytest"'),
            ('pytest | tail || echo failed', '"pytest"'),
            ('set -o pipefail; set +o pipefail; pytest | tail', '"pytest"'),
            ('cat <<EOF\nset -o pipefail\nEOF\npytest | tail', '"pytest"'),
            ('npx eslint . \\\n  | head', '"npx eslint ."'),
        ]:
            with self.subTest(command=command):
                # 同じコマンドの拒否はセッションにつき1回だけなので、検証ごとにセッションを分ける。
                self.__assert_denied(self.__invoke(command, session_id=str(uuid.uuid4())), fragment)

    def test_unmasked_commands_are_allowed(self):
        for command in [
            'set -o pipefail; dotnet test | tail -20',
            'set -euo pipefail\npytest | tail',
            'set -eo pipefail && make | tee log',
            'pytest | tail; echo "${PIPESTATUS[0]}"',
            'dotnet test',
            'dotnet test > test.log 2>&1; tail -20 test.log',
            'grep -rn test . | head',
            'ls build | wc -l',
            'cat test.log | tail',
            'git log --oneline | grep test',
            'find . -name "*test*" | head',
            'cat input.txt | pytest',
            'echo "pytest | tail"',
            'python3 - <<\'EOF\'\nimport os  # pytest | tail\nEOF',
            'pytest || true',
            'latest | tail',
            "printf 'make | tail'",
        ]:
            with self.subTest(command=command):
                self.__assert_allowed(self.__invoke(command))

    def test_other_tools_are_ignored(self):
        self.__assert_allowed(self.__invoke('dotnet test | Select-Object -Last 20', tool_name='powershell'))
        self.__assert_allowed(self.__run_hook(self.__payload(('edit', {'path': 'a.txt', 'new_str': 'pytest | tail'}))))
        self.__assert_allowed(self.__run_hook(self.__payload(('bash', {'mode': 'sync'}))))

    def test_string_args_and_prefixed_tool_name(self):
        result = self.__run_hook(self.__payload(
            ('functions.bash', json.dumps({'command': 'pytest | tail; dotnet test | head'})),
        ))
        self.__assert_denied(result, '"pytest", "dotnet test"')

    def test_same_command_is_denied_only_once(self):
        self.__assert_denied(self.__invoke('pytest | tail'))
        self.__assert_allowed(self.__invoke('pytest | tail'))
        self.__assert_denied(self.__invoke('pytest -x | tail'))
        self.__assert_denied(self.__invoke('pytest | tail', session_id='11111111-1111-4111-8111-111111111111'))
        state_directory = os.path.join(self.__root, 'ghc-agent-refinement', 'hook-states', 'block-masked-exit-codes')
        self.assertEqual(
            sorted(os.listdir(state_directory)),
            ['00000000-0000-4000-8000-000000000000.sqlite3', '11111111-1111-4111-8111-111111111111.sqlite3'],
        )

    def test_allowed_commands_create_no_state(self):
        self.__assert_allowed(self.__invoke('dotnet test'))
        self.assertFalse(os.path.exists(os.path.join(self.__root, 'ghc-agent-refinement')))

    def test_invalid_input(self):
        def encode(value):
            return json.dumps(value).encode('utf-8')

        for name, raw in [
            ('empty', b''),
            ('broken json', b'{'),
            ('invalid utf-8', b'\xff'),
            ('not object', encode([])),
            ('missing sessionId', encode({'toolName': 'bash', 'toolArgs': {}})),
            ('invalid sessionId', encode({'sessionId': 'x', 'toolName': 'bash', 'toolArgs': {}})),
            ('missing toolName', encode({'sessionId': self.__session_id, 'toolArgs': {}})),
            ('empty toolName', encode({'sessionId': self.__session_id, 'toolName': ' ', 'toolArgs': {}})),
            ('toolName not string', encode({'sessionId': self.__session_id, 'toolName': 3, 'toolArgs': {}})),
            ('missing toolArgs', encode({'sessionId': self.__session_id, 'toolName': 'bash'})),
        ]:
            with self.subTest(name=name):
                result = self.__run_hook(raw=raw)
                self.assertEqual(result.returncode, 2, name)
                self.assertEqual(result.stdout, b'', name)
                self.assertIn(b'Failed to check masked exit codes:', result.stderr)


if __name__ == '__main__':
    run_tests()
