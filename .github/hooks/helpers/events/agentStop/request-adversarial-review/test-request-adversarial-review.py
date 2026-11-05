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


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'request-adversarial-review.py')
_helpers_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
_hooks_directory = os.path.dirname(_helpers_directory)
_github_directory = os.path.dirname(_hooks_directory)
_prompt_hook = os.path.join(
    _helpers_directory, 'events', 'userPromptSubmitted', 'set-adversarial-review-state',
    'set-adversarial-review-state.py',
)


class RequestReviewTests(unittest.TestCase):
    def setUp(self):
        self.__temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.__temporary.cleanup)
        self.__repository = os.path.join(self.__temporary.name, 'work tree 日本語')
        os.mkdir(self.__repository)
        self.__environment = os.environ.copy()
        self.__environment.update(dict.fromkeys(('TMPDIR', 'TMP', 'TEMP'), self.__temporary.name))
        self.__environment.update({
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_AUTHOR_NAME': 'Hook Test', 'GIT_AUTHOR_EMAIL': 'hook@example.invalid',
            'GIT_COMMITTER_NAME': 'Hook Test', 'GIT_COMMITTER_EMAIL': 'hook@example.invalid',
        })
        self.__session_id = str(uuid.uuid4())
        self.__state_path = os.path.join(
            self.__temporary.name, 'ghc-agent-refinement', 'hook-states', 'adversarial-review',
            f'{self.__session_id}.json',
        )
        self.__git('init', '-q')
        self.__write('tracked.txt', 'original\n')
        self.__git('add', 'tracked.txt')
        self.__git('-c', 'core.hooksPath=' + os.devnull, 'commit', '-qm', 'baseline')
        self.__submit('review;;')

    def __git(self, *args):
        return subprocess.run(
            ['git', *args], cwd=self.__repository, env=self.__environment,
            capture_output=True, check=True,
        )

    def __write(self, name, content):
        with open(os.path.join(self.__repository, name), 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)

    def __invoke(self, payload, path=_hook_path):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
        return subprocess.run(
            [sys.executable, path], input=raw, capture_output=True,
            env=self.__environment, check=False,
        )

    def __submit(self, prompt):
        result = self.__invoke({'sessionId': self.__session_id, 'prompt': prompt}, _prompt_hook)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __stop(self, **extra):
        return self.__invoke({'sessionId': self.__session_id, 'cwd': self.__repository, **extra})

    def __assert_skip(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_request(self, result):
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'decision', 'reason'})
        self.assertEqual(output['decision'], 'block')
        for text in ('adversarial-review', 'staged', 'unstaged', 'untracked', 'Do not modify', ';; suffix'):
            self.assertIn(text, output['reason'])
        return output

    def test_clean_and_ignored_files(self):
        self.__assert_skip(self.__stop())
        self.__write('.gitignore', 'ignored.txt\n')
        self.__git('add', '.gitignore')
        self.__git('-c', 'core.hooksPath=' + os.devnull, 'commit', '-qm', 'ignore fixture')
        self.__write('ignored.txt', 'ignored\n')
        self.__assert_skip(self.__stop())

    def test_change_types_and_subdirectory(self):
        for kind in ('unstaged', 'staged', 'untracked', 'deleted', 'renamed'):
            with self.subTest(kind=kind):
                self.__submit('review;;')
                if kind == 'unstaged':
                    self.__write('tracked.txt', 'changed\n')
                elif kind == 'staged':
                    self.__git('add', 'tracked.txt')
                elif kind == 'untracked':
                    self.__git('-c', 'core.hooksPath=' + os.devnull, 'commit', '-qm', 'changed')
                    self.__write('new 日本語 file.txt', 'new\n')
                elif kind == 'deleted':
                    os.unlink(os.path.join(self.__repository, 'new 日本語 file.txt'))
                    os.unlink(os.path.join(self.__repository, 'tracked.txt'))
                else:
                    self.__git('restore', 'tracked.txt')
                    self.__git('mv', 'tracked.txt', 'renamed file.txt')
                nested = os.path.join(self.__repository, 'nested')
                os.makedirs(nested, exist_ok=True)
                self.__assert_request(self.__stop(cwd=nested))
                self.__assert_skip(self.__stop())

    def test_lifecycle_and_other_hook_continuation(self):
        self.__write('tracked.txt', 'changed before prompt\n')
        self.__submit('user requirements;;')
        self.__assert_request(self.__stop(stop_hook_active=True))
        self.__assert_skip(self.__stop(stop_hook_active=True))
        self.__submit('normal prompt')
        self.__assert_skip(self.__stop())
        self.__submit('second request;;')
        self.__assert_request(self.__stop())
        self.__assert_skip(self.__stop())

    def test_prefix_and_trailing_whitespace_do_not_request_review(self):
        self.__write('tracked.txt', 'changed\n')
        for prompt in (';; review', 'review;; ', 'review;;\n', 'review;;\t'):
            with self.subTest(prompt=prompt):
                self.__submit(prompt)
                self.__assert_skip(self.__stop())
        self.__submit('review;;')
        self.__assert_request(self.__stop())

    def test_clean_stop_keeps_pending_until_a_change(self):
        self.__assert_skip(self.__stop())
        self.__write('tracked.txt', 'changed during a continuation\n')
        self.__assert_request(self.__stop(stop_hook_active=True))
        self.__assert_skip(self.__stop())

    def test_missing_state_disabled_and_other_session(self):
        self.__assert_skip(self.__stop(sessionId=str(uuid.uuid4())))
        self.__submit('normal')
        self.__assert_skip(self.__stop(cwd='invalid but unused'))
        os.unlink(self.__state_path)
        self.__assert_skip(self.__stop())

    def test_subagent_does_not_consume_request(self):
        self.__write('tracked.txt', 'changed\n')
        self.__assert_skip(self.__stop(agentId='child', agentType='adversarial-review'))
        self.__assert_request(self.__stop())

    def test_invalid_inputs_and_git_failure_keep_request_pending(self):
        for payload in [
            b'', b'{', b'\xff', [], None, {},
            {'sessionId': 'bad', 'cwd': self.__repository},
            {'sessionId': self.__session_id},
            {'sessionId': self.__session_id, 'cwd': 123},
            {'sessionId': self.__session_id, 'cwd': 'relative'},
            {'sessionId': self.__session_id, 'cwd': self.__temporary.name},
            {'sessionId': self.__session_id, 'cwd': os.path.join(self.__temporary.name, 'missing')},
        ]:
            with self.subTest(payload=payload):
                result = self.__invoke(payload)
                self.assertEqual((result.returncode, result.stdout), (2, b''))
                self.assertTrue(result.stderr)
        self.__write('tracked.txt', 'changed\n')
        self.__assert_request(self.__stop())

    def test_corrupt_state(self):
        for state in ('{', 'null', '[]', '{"enabled":true}', json.dumps({
            'enabled': False, 'pending': True, 'updatedAt': 'time',
        }), json.dumps({'enabled': 1, 'pending': True, 'updatedAt': 'time'})):
            with self.subTest(state=state):
                with open(self.__state_path, 'w', encoding='utf-8', newline='\n') as file:
                    file.write(state)
                result = self.__stop()
                self.assertEqual((result.returncode, result.stdout), (2, b''))
                self.assertTrue(result.stderr)

    def test_timeout_missing_git_and_state_write_failure(self):
        namespace = runpy.run_path(_hook_path)
        for failure in (
            subprocess.TimeoutExpired(['git', 'status'], 10), FileNotFoundError('git'),
        ):
            with self.subTest(failure=failure):
                stdin = io.TextIOWrapper(io.BytesIO(json.dumps({
                    'sessionId': self.__session_id, 'cwd': self.__repository,
                }).encode('utf-8')), encoding='utf-8', newline='\n')
                stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='\n')
                stderr = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='\n')
                with (
                    patch('tempfile.gettempdir', return_value=self.__temporary.name),
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
        self.__write('tracked.txt', 'changed\n')
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps({
            'sessionId': self.__session_id, 'cwd': self.__repository,
        }).encode('utf-8')), encoding='utf-8', newline='\n')
        stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='\n')
        stderr = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='\n')
        with (
            patch('tempfile.gettempdir', return_value=self.__temporary.name),
            patch('sys.stdin', stdin), patch('sys.stdout', stdout), patch('sys.stderr', stderr),
            patch('os.replace', side_effect=PermissionError('denied')),
        ):
            self.assertEqual(namespace['main'](), 2)
            stdout.flush()
            stderr.flush()
            self.assertEqual(stdout.buffer.getvalue(), b'')
            self.assertIn(b'denied', stderr.buffer.getvalue())
        self.__assert_request(self.__stop())

    def test_configuration_and_agent_contract(self):
        with open(os.path.join(_hooks_directory, 'agentStop.json'), encoding='utf-8') as file:
            commands = json.load(file)['hooks']['agentStop']
        self.assertIn('debug-add-response-footer.py', commands[0]['bash'])
        review_commands = [command for command in commands if 'request-adversarial-review.py' in command['bash']]
        self.assertEqual(len(review_commands), 1)
        for key, interpreter in (('bash', 'python3'), ('powershell', 'python')):
            self.assertEqual(review_commands[0][key], (
                f'{interpreter} .github/hooks/helpers/runner/run-hook.py .github/hooks/helpers/events/agentStop/'
                'request-adversarial-review/request-adversarial-review.py'
            ))
        with open(os.path.join(
            _github_directory, 'agents', 'adversarial-review.agent.md',
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
    sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
    from hook_test_output import run_tests

    run_tests()
