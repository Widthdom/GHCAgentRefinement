import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-phantom-references.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']


_HOOK_TIMESTAMP = 1777777777000


def _user(content, delivery='idle'):
    return {'type': 'user.message', 'data': {'content': content, 'delivery': delivery}}


def _assistant(content):
    return {'type': 'assistant.message', 'data': {'content': content}}


class DetectPhantomReferencesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-phantom-references-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = os.path.realpath(temporary.name)
        self.__repository = os.path.join(self.__root, 'repository')
        self.__transcript_path = os.path.join(self.__root, 'events.jsonl')
        self.__write_file('src/app.py', 'one\ntwo\nthree\n')
        self.__write_file('src/pkg/module.py', 'one\ntwo')
        self.__write_file('docs/guide.md', '# guide\n')
        self.__write_file('a/README.md', '')
        self.__write_file('b/README.md', '')
        self.__write_file('src/old.py', 'old\n')
        if shutil.which('git'):
            self.__git('init', '-q')
            self.__git('add', '.')
            self.__git('commit', '-q', '-m', 'initial')
            self.__git('rm', '-q', 'src/old.py')
            self.__git('commit', '-q', '-m', 'remove old')
        # 未追跡のファイルも実在するファイルとして扱う。
        self.__write_file('src/new.py', 'new\n')
        self.__payload = {
            'sessionId': '00000000-0000-4000-8000-000000000000',
            'timestamp': _HOOK_TIMESTAMP,
            'cwd': self.__repository,
            'transcriptPath': self.__transcript_path,
        }

    def __require_git(self):
        if not shutil.which('git'):
            self.skipTest('git is not installed.')

    def __git(self, *arguments):
        subprocess.run(
            ['git', '-c', 'user.name=test', '-c', 'user.email=test@example.com', '-c', 'commit.gpgsign=false', *arguments],
            cwd=self.__repository, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )

    def __write_file(self, relative_path, content):
        path = os.path.join(self.__repository, *relative_path.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='') as file:
            file.write(content)

    def __invoke(self, events=None, payload=None, raw=None):
        if events is not None:
            with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
                hook_start = {'type': 'hook.start', 'data': {'hookType': 'agentStop', 'input': {'timestamp': _HOOK_TIMESTAMP}}}
                for event in (*events, hook_start):
                    file.write(json.dumps(event, ensure_ascii=False) + '\n')
        if raw is None:
            raw = json.dumps(self.__payload if payload is None else payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def __respond(self, response, request='確認して', payload=None):
        return self.__invoke([_user(request), _assistant(response)], payload)

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_block(self, result, *references):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(output['decision'], 'block')
        self.assertTrue(output['reason'].startswith('The final response references paths'))
        listed = [line[2:] for line in output['reason'].splitlines() if line.startswith('- ')]
        self.assertEqual(listed, list(references))
        return output['reason']

    def test_blocks_missing_paths_in_each_notation(self):
        cases = (
            ('`src/missing.py` を直した。', 'src/missing.py (not found)'),
            ('[ガイド](docs/missing.md) を参照。', 'docs/missing.md (not found)'),
            ('src/missing.py を直した。', 'src/missing.py (not found)'),
            ('`missing/dir/` を作った。', 'missing/dir/ (not found)'),
            ('`./missing.py` を見て。', './missing.py (not found)'),
            ('`../outside/x.py` を見て。', '../outside/x.py (not found)'),
            (f'`{os.path.join(self.__root, "nowhere", "x.txt")}` を見て。', f'{os.path.join(self.__root, "nowhere", "x.txt")} (not found)'),
        )
        for response, reference in cases:
            with self.subTest(response=response):
                self.__assert_block(self.__respond(response), reference)

    def test_blocks_line_numbers_beyond_file_length(self):
        cases = (
            ('`src/app.py:4`', 'src/app.py:4 (the file has only 3 lines)'),
            ('`src/app.py:2-9`', 'src/app.py:2-9 (the file has only 3 lines)'),
            ('[app](src/app.py#L2-L5)', 'src/app.py#L2-L5 (the file has only 3 lines)'),
            ('src/pkg/module.py:3 を参照。', 'src/pkg/module.py:3 (the file has only 2 lines)'),
        )
        for response, reference in cases:
            with self.subTest(response=response):
                self.__assert_block(self.__respond(response), reference)

    def test_allows_existing_references(self):
        responses = (
            '`src/app.py:3` と `src/app.py#L1-L3` と [guide](docs/guide.md) を更新した。',
            'src/app.py を更新した。',
            'src/app.py. の末尾にピリオドがあっても実在する。',
            '`src/` と `./src/app.py` と `src/new.py` を見た。',
            f'`{os.path.join(self.__repository, "src", "app.py")}:2` を見た。',
        )
        for response in responses:
            with self.subTest(response=response):
                self.__assert_silent(self.__respond(response))

    def test_abbreviated_paths_match_tracked_files(self):
        self.__require_git()
        self.__assert_silent(self.__respond('`pkg/module.py:2` と `src/pkg/` を見た。'))
        self.__assert_block(self.__respond('`pkg/module.py:5` を見た。'), 'pkg/module.py:5 (the file has only 2 lines)')
        # 複数のファイルに一致する省略表記は、行番号を検証しない。
        self.__assert_silent(self.__respond('`README.md` と `/README.md:99` を見た。'))
        self.__assert_block(self.__respond('`pkg/other.py` を見た。'), 'pkg/other.py (not found)')

    def test_repository_root_paths_resolve_from_subdirectory(self):
        self.__require_git()
        payload = {**self.__payload, 'cwd': os.path.join(self.__repository, 'src')}
        self.__assert_silent(self.__respond('`docs/guide.md` と `app.py` と `pkg/module.py` を見た。', payload=payload))

    def test_paths_in_git_history_are_allowed(self):
        self.__require_git()
        for response in ('`src/old.py` を削除した。', '`./src/old.py` を削除した。', 'old.py を削除した。'):
            with self.subTest(response=response):
                self.__assert_silent(self.__respond(response))

    def test_names_starting_with_dots_are_inside_repository(self):
        # `..legacy/` は親ディレクトリではなく、リポジトリ内のフォルダー。
        self.__require_git()
        self.__write_file('..legacy/x.py', 'x\n')
        self.__git('add', '.')
        self.__git('commit', '-q', '-m', 'add legacy')
        self.__git('rm', '-q', '..legacy/x.py')
        self.__git('commit', '-q', '-m', 'remove legacy')
        self.__assert_silent(self.__respond('`./..legacy/x.py` を削除した。'))

    def test_ignores_non_path_notations(self):
        responses = (
            '```\nsrc/missing.py\n```\n',
            '`https://example.com/missing.py` と [link](https://example.com/a/b.md) と [anchor](#section)',
            '`github.com/owner/repo.git`',
            '`.github/hooks/<hook-name>/x.py` と `src/*.py` と `src/{a,b}.py` と `$HOME/x.py`',
            '`/update-hook` と `origin/main` と `missing.py` と `git -C src/missing.py`',
            'and/or と TCP/IP と 2026/10/28 と feature/new-branch',
            '`tempfile.gettempdir()` と `os.path.realpath`',
        )
        for response in responses:
            with self.subTest(response=response):
                self.__assert_silent(self.__respond(response))

    def test_paths_marked_as_planned_are_allowed(self):
        responses = (
            '`src/planned.py`(planned)に記録する。',
            '`src/planned.py` ( planned ) に記録する。',
            'src/planned.py(planned) に記録する。',
            '[設計](docs/plan.md) (planned) を書く。',
            '`src/planned.py` (Not Yet Created)',
            '`src/planned.py` (NEW)',
            '`src/planned.py` (planned) を作り、あとで `src/planned.py` を読む。',
        )
        for response in responses:
            with self.subTest(response=response):
                self.__assert_silent(self.__respond(response))
        # 目印は対象のパスにだけ効き、離れた位置の目印、別のパスの目印、英語以外の目印では除外しない。
        cases = (
            '`src/planned.py` (planned) と `src/missing.py` を見た。',
            '`src/missing.py` を見た。(planned)',
            '`src/missing.py` (created)',
            '`src/missing.py`（planned）',
            '`src/missing.py`（未作成）',
        )
        for response in cases:
            with self.subTest(response=response):
                self.__assert_block(self.__respond(response), 'src/missing.py (not found)')

    def test_reason_requests_planned_marker(self):
        reason = self.__assert_block(self.__respond('`src/missing.py`'), 'src/missing.py (not found)')
        self.assertIn('append the marker (planned) right after the path', reason)

    def test_paths_in_user_request_are_allowed(self):
        self.__assert_silent(self.__respond('`src/missing.py` は存在しない。', request='src/missing.py を開いて'))

    def test_reports_at_most_ten_references(self):
        response = ' '.join(f'`src/missing{index}.py`' for index in range(12))
        self.__assert_block(self.__respond(response), *(f'src/missing{index}.py (not found)' for index in range(10)))

    def test_without_git_checks_existence_only(self):
        directory = os.path.join(self.__root, 'plain')
        os.makedirs(os.path.join(directory, 'src'))
        with open(os.path.join(directory, 'src', 'a.py'), 'w', encoding='utf-8', newline='\n') as file:
            file.write('a\n')
        payload = {**self.__payload, 'cwd': directory}
        self.__assert_silent(self.__respond('`src/a.py:1` を見た。', payload=payload))
        self.__assert_block(self.__respond('`src/b.py` を見た。', payload=payload), 'src/b.py (not found)')

    def test_blocks_only_once_per_request(self):
        first = [_user('直して'), _assistant('`src/missing.py` を直した。')]
        reason = self.__assert_block(self.__invoke(first), 'src/missing.py (not found)')
        self.__assert_silent(self.__invoke([*first, _user(reason, 'queued'), _assistant('`src/missing.py` を直した。')]))
        self.__assert_silent(self.__invoke([*first, _user('割り込み', 'queued'), _assistant('`src/app.py` を直した。')]))
        self.__assert_block(
            self.__invoke([*first, _user(reason, 'queued'), _user('次の依頼'), _assistant('`src/missing.py`')]),
            'src/missing.py (not found)',
        )

    def test_skips_subagents_and_missing_responses(self):
        self.__assert_silent(self.__respond('`src/missing.py`', payload={**self.__payload, 'agentId': 'child-agent'}))
        self.__assert_silent(self.__invoke([_assistant('`src/missing.py`'), _user('次の依頼')]))

    def test_invalid_input_reports_error(self):
        self.__respond('`src/app.py`')
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'\xff']
        for value in (None, '', ' ', 3, [], os.path.join(self.__root, 'missing.jsonl'), self.__root):
            invalid.append(json.dumps({**self.__payload, 'transcriptPath': value}).encode('utf-8'))
        for value in (None, '', 3, 'relative', os.path.join(self.__root, 'missing-directory')):
            invalid.append(json.dumps({**self.__payload, 'cwd': value}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertTrue(result.stderr)

    def test_invalid_transcript_reports_error(self):
        for content in ('{', '[]\n', '{"type":"user.message","data":[]}\n'):
            with self.subTest(content=content):
                with open(self.__transcript_path, 'w', encoding='utf-8', newline='\n') as file:
                    file.write(content)
                result = self.__invoke(payload={**self.__payload, 'timestamp': None})
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to inspect path references:', result.stderr)


if __name__ == '__main__':
    _run_tests()
