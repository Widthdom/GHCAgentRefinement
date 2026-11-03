import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'check-cross-agent-skill-wrappers.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
_repository_root = os.path.dirname(os.path.dirname(_hooks_directory))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_WRAPPER_BODY = (
    '[正本のskill](../../../.github/skills/{name}/SKILL.md) を読み、その手順に従う。'
    '正本内の相対パスは、正本の `SKILL.md` があるディレクトリを基準に解決する。'
)
_CANONICAL = '.github/skills/sample/SKILL.md'
_CODEX_WRAPPER = '.agents/skills/sample/SKILL.md'
_CLAUDE_WRAPPER = '.claude/skills/sample/SKILL.md'


class CheckCrossAgentSkillWrappersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-check-cross-agent-skill-wrappers-test-')
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update({'TMPDIR': self.root, 'TEMP': self.root, 'TMP': self.root})
        self.session_id = str(uuid.uuid4())
        self.state_path = os.path.join(
            self.root, 'ghc-agent-refinement', 'hook-states', 'check-cross-agent-skill-wrappers', f'{self.session_id}.sqlite3',
        )
        self.workspace = os.path.join(self.root, 'workspace')
        self.write_skill('sample', "'サンプルを作るときに使う。'")

    def write(self, relative_path, content, newline='\n'):
        path = os.path.join(self.workspace, *relative_path.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as file:
            file.write(content.replace('\n', newline).encode('utf-8'))

    def write_skill(self, name, description, body='手順。'):
        frontmatter = f'---\nname: {name}\ndescription: {description}\n---\n\n'
        self.write(f'.github/skills/{name}/SKILL.md', frontmatter + body + '\n')
        for directory in ('.agents', '.claude'):
            self.write(f'{directory}/skills/{name}/SKILL.md', frontmatter + _WRAPPER_BODY.format(name=name) + '\n')

    def invoke(self, path=_CANONICAL, tool_name='edit', tool_args=None, raw=None):
        if raw is None:
            payload = {
                'sessionId': self.session_id,
                'timestamp': 0,
                'cwd': self.workspace,
                'toolName': tool_name,
                'toolArgs': {'path': path} if tool_args is None else tool_args,
                'toolResult': {'resultType': 'success', 'textResultForLlm': 'File updated.'},
            }
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment, timeout=20,
        )

    def assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def assert_problems(self, result, problems):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        output = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(set(output), {'additionalContext'})
        context = output['additionalContext']
        listed = context.split('out of sync: ', 1)[1].split('. Per ', 1)[0]
        self.assertEqual(sorted(listed.split('; ')), sorted(problems))
        self.assertIn(_WRAPPER_BODY.format(name='<name>'), context)

    def test_configuration(self):
        with open(os.path.join(_hooks_directory, 'postToolUse.json'), encoding='utf-8') as file:
            configuration = json.load(file)
        relative_path = '.github/hooks/helpers/events/postToolUse/check-cross-agent-skill-wrappers/check-cross-agent-skill-wrappers.py'
        entries = [
            entry for entry in configuration['hooks']['postToolUse']
            if entry.get('bash') == f'python3 .github/hooks/helpers/runner/run-hook.py {relative_path}'
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['type'], 'command')
        self.assertEqual(entries[0]['powershell'], f'python .github/hooks/helpers/runner/run-hook.py {relative_path}')
        self.assertEqual(
            entries[0]['matcher'], '(?:functions\\.)?(?:edit|create|insert|str_replace_editor|apply_patch)',
        )

    def test_repository_skills_are_in_sync(self):
        # このリポジトリのskillが規約どおりであることを、hook自身の判定で確かめる。
        names = sorted(os.listdir(os.path.join(_repository_root, '.github', 'skills')))
        self.assertTrue(names)
        for name in names:
            with self.subTest(name=name):
                self.workspace = _repository_root
                self.assert_silent(self.invoke(f'.claude/skills/{name}/SKILL.md'))
        self.assertFalse(os.path.exists(self.state_path))

    def test_in_sync_skill_is_silent(self):
        for path in (_CANONICAL, _CODEX_WRAPPER, _CLAUDE_WRAPPER):
            with self.subTest(path=path):
                self.assert_silent(self.invoke(path))
        self.assertFalse(os.path.exists(self.state_path))

    def test_crlf_and_bom_are_accepted(self):
        content = f"---\nname: sample\ndescription: 'サンプルを作るときに使う。'\n---\n\n{_WRAPPER_BODY.format(name='sample')}\n"
        # 先頭にUTF-8 BOM（U+FEFF）を付ける。見えない文字のため、コード上ではchrで書く。
        self.write(_CLAUDE_WRAPPER, chr(0xFEFF) + content, newline='\r\n')
        self.assert_silent(self.invoke(_CLAUDE_WRAPPER))

    def test_description_change_without_wrappers(self):
        self.write(_CANONICAL, "---\nname: sample\ndescription: '新しい説明。'\n---\n\n手順。\n")
        self.assert_problems(self.invoke(_CANONICAL), [
            f'{_CODEX_WRAPPER} has a description that differs from {_CANONICAL}',
            f'{_CLAUDE_WRAPPER} has a description that differs from {_CANONICAL}',
        ])

    def test_wrapper_problems(self):
        self.write(_CODEX_WRAPPER, "---\nname: other\ndescription: 'サンプルを作るときに使う。'\n---\n\n手順。\n")
        os.remove(os.path.join(self.workspace, *_CLAUDE_WRAPPER.split('/')))
        self.assert_problems(self.invoke(_CODEX_WRAPPER), [
            f'{_CODEX_WRAPPER} has a name that differs from {_CANONICAL}',
            f'{_CODEX_WRAPPER} has a body other than the standard wrapper body',
            f'{_CLAUDE_WRAPPER} is missing',
        ])

    def test_new_canonical_without_wrappers(self):
        self.write('.github/skills/new-skill/SKILL.md', "---\nname: new-skill\ndescription: '新しい。'\n---\n\n手順。\n")
        self.assert_problems(self.invoke('.github/skills/new-skill/SKILL.md', tool_name='create'), [
            '.agents/skills/new-skill/SKILL.md is missing',
            '.claude/skills/new-skill/SKILL.md is missing',
        ])

    def test_canonical_name_must_match_folder(self):
        content = "---\nname: renamed\ndescription: 'サンプルを作るときに使う。'\n---\n\n"
        self.write(_CANONICAL, content + '手順。\n')
        for path in (_CODEX_WRAPPER, _CLAUDE_WRAPPER):
            self.write(path, content + _WRAPPER_BODY.format(name='sample') + '\n')
        self.assert_problems(self.invoke(_CANONICAL), [
            f"{_CANONICAL} has name 'renamed', which does not match its folder name",
        ])

    def test_deleted_canonical_with_remaining_wrappers(self):
        os.remove(os.path.join(self.workspace, *_CANONICAL.split('/')))
        patch = f'*** Begin Patch\n*** Delete File: {_CANONICAL}\n*** End Patch\n'
        self.assert_problems(self.invoke(tool_name='apply_patch', tool_args=patch), [
            f'{_CODEX_WRAPPER} exists but its canonical skill {_CANONICAL} does not',
            f'{_CLAUDE_WRAPPER} exists but its canonical skill {_CANONICAL} does not',
        ])
        # ラッパーも消せば、skill自体の削除として扱う。
        for path in (_CODEX_WRAPPER, _CLAUDE_WRAPPER):
            shutil.rmtree(os.path.dirname(os.path.join(self.workspace, *path.split('/'))))
        self.assert_silent(self.invoke(tool_name='apply_patch', tool_args=patch))

    def test_notifies_once_and_again_after_resolution(self):
        self.write(_CANONICAL, "---\nname: sample\ndescription: '新しい説明。'\n---\n\n手順。\n")
        problems = [
            f'{_CODEX_WRAPPER} has a description that differs from {_CANONICAL}',
            f'{_CLAUDE_WRAPPER} has a description that differs from {_CANONICAL}',
        ]
        self.assert_problems(self.invoke(_CANONICAL), problems)
        self.assert_silent(self.invoke(_CANONICAL))
        # 片方のラッパーだけ直した途中の状態では、新しい問題がないため通知しない。
        self.write(_CODEX_WRAPPER, f"---\nname: sample\ndescription: '新しい説明。'\n---\n\n{_WRAPPER_BODY.format(name='sample')}\n")
        self.assert_silent(self.invoke(_CODEX_WRAPPER))
        # 解消後に再び崩れたら、改めて通知する。
        self.write_skill('sample', "'新しい説明。'")
        self.assert_silent(self.invoke(_CLAUDE_WRAPPER))
        self.write(_CANONICAL, "---\nname: sample\ndescription: 'さらに新しい説明。'\n---\n\n手順。\n")
        self.assert_problems(self.invoke(_CANONICAL), problems)

    def test_patch_with_multiple_skills(self):
        self.write_skill('other', "'別のskill。'")
        self.write('.github/skills/other/SKILL.md', "---\nname: other\ndescription: '変更。'\n---\n\n手順。\n")
        patch = (
            '*** Begin Patch\n'
            f'*** Update File: {_CANONICAL}\n@@\n-a\n+b\n'
            '*** Update File: .github/skills/other/SKILL.md\n@@\n-a\n+b\n'
            '*** End Patch\n'
        )
        self.assert_problems(self.invoke(tool_name='functions.apply_patch', tool_args=patch), [
            '.agents/skills/other/SKILL.md has a description that differs from .github/skills/other/SKILL.md',
            '.claude/skills/other/SKILL.md has a description that differs from .github/skills/other/SKILL.md',
        ])

    def test_unrelated_files_and_tools_are_ignored(self):
        os.remove(os.path.join(self.workspace, *_CLAUDE_WRAPPER.split('/')))
        self.write('.github/skills/sample/scripts/run.py', 'print()\n')
        for tool_name, path in (
            ('edit', '.github/skills/sample/scripts/run.py'),
            ('edit', '.github/skills/sample/README.md'),
            ('edit', 'skills/sample/SKILL.md'),
            ('view', _CANONICAL),
        ):
            with self.subTest(tool_name=tool_name, path=path):
                self.assert_silent(self.invoke(path, tool_name=tool_name))
        self.assertFalse(os.path.exists(self.state_path))

    def test_invalid_input(self):
        valid = {'sessionId': self.session_id, 'cwd': self.workspace, 'toolName': 'edit', 'toolArgs': {}}
        cases = {
            'empty': b'',
            'not json': b'{',
            'not object': b'[]',
            'invalid session': json.dumps({**valid, 'sessionId': 'x'}).encode('utf-8'),
            'relative cwd': json.dumps({**valid, 'cwd': 'relative'}).encode('utf-8'),
            'missing tool name': json.dumps({**valid, 'toolName': ''}).encode('utf-8'),
            'missing tool args': json.dumps({key: value for key, value in valid.items() if key != 'toolArgs'}).encode('utf-8'),
            'invalid utf-8': b'\xff',
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                result = self.invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to check cross-agent skill wrappers:', result.stderr)


if __name__ == '__main__':
    run_tests()
