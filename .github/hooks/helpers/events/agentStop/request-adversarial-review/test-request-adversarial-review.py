import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


script_directory = os.path.dirname(os.path.abspath(__file__))
hook_path = os.path.join(script_directory, 'request-adversarial-review.py')
helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))
hooks_directory = os.path.dirname(helpers_directory)
github_directory = os.path.dirname(hooks_directory)
prompt_hook = os.path.join(
    helpers_directory, 'events', 'userPromptSubmitted', 'set-adversarial-review-state',
    'set-adversarial-review-state.py',
)


class RequestReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = os.path.join(self.temporary.name, 'work tree 日本語')
        os.mkdir(self.repository)
        self.environment = os.environ.copy()
        self.environment.update(dict.fromkeys(('TMPDIR', 'TMP', 'TEMP'), self.temporary.name))
        self.environment.update({
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_AUTHOR_NAME': 'Hook Test', 'GIT_AUTHOR_EMAIL': 'hook@example.invalid',
            'GIT_COMMITTER_NAME': 'Hook Test', 'GIT_COMMITTER_EMAIL': 'hook@example.invalid',
        })
        self.session_id = str(uuid.uuid4())
        self.state_path = os.path.join(
            self.temporary.name, 'ghc-agent-refinement', 'adversarial-review',
            f'{self.session_id}.json',
        )
        self.git('init', '-q')
        self.write('tracked.txt', 'original\n')
        self.git('add', 'tracked.txt')
        self.git('-c', 'core.hooksPath=' + os.devnull, 'commit', '-qm', 'baseline')
        self.submit('review;;')

    def git(self, *args):
        return subprocess.run(
            ['git', *args], cwd=self.repository, env=self.environment,
            capture_output=True, check=True,
        )

    def write(self, name, content):
        with open(os.path.join(self.repository, name), 'w', encoding='utf-8') as file:
            file.write(content)

    def invoke(self, payload, path=hook_path):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
        return subprocess.run(
            [sys.executable, path], input=raw, capture_output=True,
            env=self.environment, check=False,
        )

    def submit(self, prompt):
        result = self.invoke({'sessionId': self.session_id, 'prompt': prompt}, prompt_hook)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def stop(self, **extra):
        return self.invoke({'sessionId': self.session_id, 'cwd': self.repository, **extra})

    def assert_skip(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_request(self, result):
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'decision', 'reason'})
        self.assertEqual(output['decision'], 'block')
        for text in ('adversarial-review', 'staged', 'unstaged', 'untracked', 'Do not modify', ';; suffix'):
            self.assertIn(text, output['reason'])
        return output

    def test_clean_and_ignored_files(self):
        self.assert_skip(self.stop())
        self.write('.gitignore', 'ignored.txt\n')
        self.git('add', '.gitignore')
        self.git('-c', 'core.hooksPath=' + os.devnull, 'commit', '-qm', 'ignore fixture')
        self.write('ignored.txt', 'ignored\n')
        self.assert_skip(self.stop())

    def test_change_types_and_subdirectory(self):
        for kind in ('unstaged', 'staged', 'untracked', 'deleted', 'renamed'):
            with self.subTest(kind=kind):
                self.submit('review;;')
                if kind == 'unstaged':
                    self.write('tracked.txt', 'changed\n')
                elif kind == 'staged':
                    self.git('add', 'tracked.txt')
                elif kind == 'untracked':
                    self.git('-c', 'core.hooksPath=' + os.devnull, 'commit', '-qm', 'changed')
                    self.write('new 日本語 file.txt', 'new\n')
                elif kind == 'deleted':
                    os.unlink(os.path.join(self.repository, 'new 日本語 file.txt'))
                    os.unlink(os.path.join(self.repository, 'tracked.txt'))
                else:
                    self.git('restore', 'tracked.txt')
                    self.git('mv', 'tracked.txt', 'renamed file.txt')
                nested = os.path.join(self.repository, 'nested')
                os.makedirs(nested, exist_ok=True)
                self.assert_request(self.stop(cwd=nested))
                self.assert_skip(self.stop())

    def test_lifecycle_and_other_hook_continuation(self):
        self.write('tracked.txt', 'changed before prompt\n')
        self.submit('user requirements;;')
        self.assert_request(self.stop(stop_hook_active=True))
        self.assert_skip(self.stop(stop_hook_active=True))
        self.submit('normal prompt')
        self.assert_skip(self.stop())
        self.submit('second request;;')
        self.assert_request(self.stop())
        self.assert_skip(self.stop())

    def test_prefix_and_trailing_whitespace_do_not_request_review(self):
        self.write('tracked.txt', 'changed\n')
        for prompt in (';; review', 'review;; ', 'review;;\n', 'review;;\t'):
            with self.subTest(prompt=prompt):
                self.submit(prompt)
                self.assert_skip(self.stop())
        self.submit('review;;')
        self.assert_request(self.stop())

    def test_clean_stop_keeps_pending_until_a_change(self):
        self.assert_skip(self.stop())
        self.write('tracked.txt', 'changed during a continuation\n')
        self.assert_request(self.stop(stop_hook_active=True))
        self.assert_skip(self.stop())

    def test_missing_state_disabled_and_other_session(self):
        self.assert_skip(self.stop(sessionId=str(uuid.uuid4())))
        self.submit('normal')
        self.assert_skip(self.stop(cwd='invalid but unused'))
        os.unlink(self.state_path)
        self.assert_skip(self.stop())

    def test_subagent_does_not_consume_request(self):
        self.write('tracked.txt', 'changed\n')
        self.assert_skip(self.stop(agentId='child', agentType='adversarial-review'))
        self.assert_request(self.stop())

    def test_invalid_inputs_and_git_failure_keep_request_pending(self):
        for payload in [
            b'', b'{', b'\xff', [], None, {},
            {'sessionId': 'bad', 'cwd': self.repository},
            {'sessionId': self.session_id},
            {'sessionId': self.session_id, 'cwd': 123},
            {'sessionId': self.session_id, 'cwd': 'relative'},
            {'sessionId': self.session_id, 'cwd': self.temporary.name},
            {'sessionId': self.session_id, 'cwd': os.path.join(self.temporary.name, 'missing')},
        ]:
            with self.subTest(payload=payload):
                result = self.invoke(payload)
                self.assertEqual((result.returncode, result.stdout), (2, b''))
                self.assertTrue(result.stderr)
        self.write('tracked.txt', 'changed\n')
        self.assert_request(self.stop())

    def test_corrupt_state(self):
        for state in ('{', 'null', '[]', '{"enabled":true}', json.dumps({
            'enabled': False, 'pending': True, 'updatedAt': 'time',
        }), json.dumps({'enabled': 1, 'pending': True, 'updatedAt': 'time'})):
            with self.subTest(state=state):
                with open(self.state_path, 'w', encoding='utf-8') as file:
                    file.write(state)
                result = self.stop()
                self.assertEqual((result.returncode, result.stdout), (2, b''))
                self.assertTrue(result.stderr)

    def test_timeout_missing_git_and_state_write_failure(self):
        namespace = runpy.run_path(hook_path)
        for failure in (
            subprocess.TimeoutExpired(['git', 'status'], 10), FileNotFoundError('git'),
        ):
            with self.subTest(failure=failure):
                stdin = io.TextIOWrapper(io.BytesIO(json.dumps({
                    'sessionId': self.session_id, 'cwd': self.repository,
                }).encode('utf-8')), encoding='utf-8')
                stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
                stderr = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
                with (
                    patch('tempfile.gettempdir', return_value=self.temporary.name),
                    patch('sys.stdin', stdin), patch('sys.stdout', stdout),
                    patch('sys.stderr', stderr),
                    patch('subprocess.run', side_effect=failure) as run,
                ):
                    code = namespace['main']()
                    stdout.flush()
                    stderr.flush()
                    self.assertEqual(code, 2)
                    self.assertEqual(stdout.buffer.getvalue(), b'')
                    if isinstance(failure, subprocess.TimeoutExpired):
                        self.assertIn(b'git status timed out:', stderr.buffer.getvalue())
                    else:
                        self.assertIn(b'Failed to run git status:', stderr.buffer.getvalue())
                    self.assertEqual(run.call_args.kwargs['timeout'], 10)
                    self.assertEqual(run.call_args.args[0], [
                        'git', 'status', '--porcelain=v1', '--untracked-files=all', '-z',
                    ])
        self.write('tracked.txt', 'changed\n')
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps({
            'sessionId': self.session_id, 'cwd': self.repository,
        }).encode('utf-8')), encoding='utf-8')
        stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
        stderr = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
        with (
            patch('tempfile.gettempdir', return_value=self.temporary.name),
            patch('sys.stdin', stdin), patch('sys.stdout', stdout), patch('sys.stderr', stderr),
            patch('os.replace', side_effect=PermissionError('denied')),
        ):
            self.assertEqual(namespace['main'](), 2)
            stdout.flush()
            stderr.flush()
            self.assertEqual(stdout.buffer.getvalue(), b'')
            self.assertIn(b'denied', stderr.buffer.getvalue())
        self.assert_request(self.stop())

    def test_configuration_and_agent_contract(self):
        with open(os.path.join(hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            commands = json.load(file)['hooks']['agentStop']
        self.assertIn('debug-add-response-footer.py', commands[0]['bash'])
        for key, interpreter in (('bash', 'python3'), ('powershell', 'python')):
            self.assertEqual(commands[-1][key], (
                f'{interpreter} .github/hooks/helpers/events/agentStop/'
                'request-adversarial-review/request-adversarial-review.py'
            ))
        with open(os.path.join(
            github_directory, 'agents', 'adversarial-review.agent.md',
        ), encoding='utf-8') as file:
            agent = file.read()
        frontmatter = agent.split('---', 2)[1]
        for field in (
            'name: adversarial-review', 'description:', 'tools: [read, search, execute]',
            'agents: []', 'user-invocable: false',
        ):
            self.assertIn(field, frontmatter)
        self.assertNotIn('model:', frontmatter)
        self.assertIn('git diff --cached', agent)
        self.assertIn('No findings', agent)
        self.assertIn('Not verified', agent)


if __name__ == '__main__':
    unittest.main()
