import io
import json
import os
import re
import runpy
import sqlite3
import sys
import tempfile
import uuid
from contextlib import closing
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


# SDKがファイル書き込みとして扱う組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$', re.MULTILINE)
# 正本と、それを参照するラッパーの配置。リポジトリルートからの相対ディレクトリ。
_CANONICAL_DIRECTORY = ('.github', 'skills')
_WRAPPER_DIRECTORIES = (('.agents', 'skills'), ('.claude', 'skills'))
_SKILL_FILENAME = 'SKILL.md'
_FRONTMATTER_KEYS = ('name', 'description')
_WRAPPER_BODY = (
    '[正本のskill](../../../.github/skills/{name}/SKILL.md) を読み、その手順に従う。'
    '正本内の相対パスは、正本の `SKILL.md` があるディレクトリを基準に解決する。'
)
_SYNC_CONTEXT = (
    'The following skill files are out of sync: {problems}. '
    'Per .github/skills/create-skill/references/skill-conventions.md, every canonical skill '
    '.github/skills/<name>/SKILL.md must have wrappers .agents/skills/<name>/SKILL.md and '
    '.claude/skills/<name>/SKILL.md with the same name and description, and the wrapper body must only be: '
    '{body} Do not copy steps or supporting files into wrappers. If you are in the middle of updating these '
    'files, finish the update; otherwise fix them.'
)


def _get_edited_paths(tool_name, tool_args, cwd):
    if tool_name == 'apply_patch':
        # 削除・移動元も含める。正本を消したときに、残ったラッパーを検出するため。
        paths = [match for text in _collect_json_string_values(tool_args) for match in _PATCH_FILE_PATTERN.findall(text)]
    else:
        path = tool_args.get('path') if isinstance(tool_args, dict) else None
        paths = [path] if isinstance(path, str) and path.strip() else []
    return sorted({os.path.realpath(os.path.join(cwd, path)) for path in paths})


def _get_skill(path):
    # <ルート>/.github/skills/<name>/SKILL.md などに一致すれば (ルート, skill名) を返す。
    # skillの補助ファイル（scripts/ など）はラッパーと対応しないため対象外。
    directory, filename = os.path.split(path)
    if os.path.normcase(filename) != os.path.normcase(_SKILL_FILENAME):
        return None
    skills_directory, name = os.path.split(directory)
    for parts in (_CANONICAL_DIRECTORY, *_WRAPPER_DIRECTORIES):
        root = skills_directory
        for part in reversed(parts):
            root, actual = os.path.split(root)
            if os.path.normcase(actual) != os.path.normcase(part):
                break
        else:
            return root, name
    return None


def _read_skill_file(path):
    # 戻り値は (frontmatterの辞書, 本文)。frontmatterがなければ空の辞書を返す。ファイルがなければNone。
    try:
        with open(path, 'rb') as file:
            text = file.read().decode('utf-8-sig')
    except FileNotFoundError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return {}, text.strip()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == '---':
            break
    else:
        return {}, text.strip()
    frontmatter = {}
    for line in lines[1:index]:
        key, separator, value = line.partition(':')
        if separator and key.strip() in _FRONTMATTER_KEYS:
            # 引用符も含めて比べ、正本とラッパーで書き方まで揃える。
            frontmatter[key.strip()] = value.strip()
    return frontmatter, '\n'.join(lines[index + 1:]).strip()


def _get_display_path(*parts):
    return '/'.join(parts)


def _get_problems(root, name):
    canonical_display = _get_display_path(*_CANONICAL_DIRECTORY, name, _SKILL_FILENAME)
    canonical = _read_skill_file(os.path.join(root, *_CANONICAL_DIRECTORY, name, _SKILL_FILENAME))
    wrappers = [
        (_get_display_path(*parts, name, _SKILL_FILENAME), _read_skill_file(os.path.join(root, *parts, name, _SKILL_FILENAME)))
        for parts in _WRAPPER_DIRECTORIES
    ]
    if canonical is None:
        # 正本がないのにラッパーだけが残っている場合。どちらもなければskill自体が削除済み。
        return [
            f'{display} exists but its canonical skill {canonical_display} does not'
            for display, wrapper in wrappers if wrapper is not None
        ]
    problems = []
    canonical_frontmatter, _ = canonical
    canonical_name = canonical_frontmatter.get('name')
    if canonical_name is None or canonical_name.strip('\'"') != name:
        problems.append(f'{canonical_display} has name {canonical_name!r}, which does not match its folder name')
    body = _WRAPPER_BODY.format(name=name)
    for display, wrapper in wrappers:
        if wrapper is None:
            problems.append(f'{display} is missing')
            continue
        frontmatter, wrapper_body = wrapper
        for key in _FRONTMATTER_KEYS:
            if frontmatter.get(key) != canonical_frontmatter.get(key):
                problems.append(f'{display} has a {key} that differs from {canonical_display}')
        if wrapper_body != body:
            problems.append(f'{display} has a body other than the standard wrapper body')
    return problems


def _record_notifications(state_path, results):
    # results：(skillのキー, 問題のリスト) のリスト。戻り値は、今回初めて通知する問題を含むskillの問題のリスト。
    new_problems = []
    # SQLiteのトランザクションで、並列に編集しても同じ問題を重複して通知しない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS notifications ('
            'skill TEXT NOT NULL, '
            'problem TEXT NOT NULL, '
            'PRIMARY KEY (skill, problem))'
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for skill_key, problems in results:
                # 解消した問題は記録を消し、再び崩れたときに改めて通知する。
                stored = connection.execute('SELECT problem FROM notifications WHERE skill = ?', (skill_key,)).fetchall()
                for (problem,) in stored:
                    if problem not in problems:
                        connection.execute(
                            'DELETE FROM notifications WHERE skill = ? AND problem = ?', (skill_key, problem),
                        )
                unnotified = [
                    problem for problem in problems
                    if connection.execute(
                        'INSERT OR IGNORE INTO notifications (skill, problem) VALUES (?, ?)', (skill_key, problem),
                    ).rowcount
                ]
                if unnotified:
                    new_problems.extend(problems)
    return new_problems


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')

        session_id = hook_input.get('sessionId')
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('sessionId must be a non-empty UUID string.')
        try:
            parsed_session_id = uuid.UUID(session_id.strip())
        except ValueError:
            raise ValueError('sessionId must be a valid UUID.') from None

        for field in ('cwd', 'toolName'):
            value = hook_input.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{field} must be a non-empty string.')
        if not os.path.isabs(hook_input['cwd']):
            raise ValueError('cwd must be an absolute path.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        tool_name = hook_input['toolName'].removeprefix('functions.')
        if tool_name not in _EDIT_TOOL_NAMES:
            return 0
        paths = _get_edited_paths(tool_name, _unwrap_tool_args(hook_input['toolArgs']), hook_input['cwd'])
        skills = sorted({skill for skill in map(_get_skill, paths) if skill is not None})
        if not skills:
            return 0

        # 状態のキーはルートとskill名。別のリポジトリの同名skillと区別する。
        results = [
            (f'{os.path.normcase(root)}\0{name}', _get_problems(root, name)) for root, name in skills
        ]
        # hookは呼び出しごとに別プロセスなので、通知済みの問題をセッションUUID別に保存する。
        state_directory = os.path.join(
            tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'check-cross-agent-skill-wrappers',
        )
        state_path = os.path.join(state_directory, f'{parsed_session_id}.sqlite3')
        problems = []
        # 問題があるか、解消した記録を消す必要がある場合だけ状態を開く。
        if any(skill_problems for _, skill_problems in results) or os.path.isfile(state_path):
            os.makedirs(state_directory, exist_ok=True)
            problems = _record_notifications(state_path, results)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check cross-agent skill wrappers: {ex}', file=sys.stderr)
        return 2

    if problems:
        # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
        print(json.dumps(
            {'additionalContext': _SYNC_CONTEXT.format(problems='; '.join(problems), body=_WRAPPER_BODY.format(name='<name>'))},
            ensure_ascii=False,
        ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
