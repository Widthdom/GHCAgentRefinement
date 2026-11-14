import io
import os
import runpy
import sqlite3
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_tool_kinds = runpy.run_path(os.path.join(_utilities_directory, 'check-tool-kinds', 'check-tool-kinds.py'))
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_get_edited_paths = runpy.run_path(os.path.join(_utilities_directory, 'edit-tool-changes', 'edit-tool-changes.py'))['get_edited_paths']
_filter_new_notifications = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-new-notifications', 'filter-new-notifications.py',
))['filter_new_notifications']


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


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()

        state_path = _get_state_path('check-cross-agent-skill-wrappers', hook_input.get('sessionId'))

        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        if not _tool_kinds['is_edit_tool'](tool_name):
            return 0
        # パッチの削除・移動元も含める。正本を消したときに、残ったラッパーを検出するため。
        paths = {os.path.realpath(os.path.join(cwd, path)) for path in _get_edited_paths(tool_name, tool_args)}
        skills = sorted({skill for skill in map(_get_skill, paths) if skill is not None})
        if not skills:
            return 0

        # 状態のキーはルートとskill名。別のリポジトリの同名skillと区別する。
        results = []
        for root, name in skills:
            skill_problems = _get_problems(root, name)
            results.append((f'{os.path.normcase(root)}\0{name}', skill_problems, skill_problems))
        # hookは呼び出しごとに別プロセスなので、通知済みの問題をセッションUUID別に保存する。
        problems = []
        # 問題があるか、解消した記録を消す必要がある場合だけ状態を開く。
        if any(skill_problems for _, _, skill_problems in results) or os.path.isfile(state_path):
            problems = [
                problem for skill_problems in _filter_new_notifications(state_path, results) for problem in skill_problems
            ]
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check cross-agent skill wrappers: {ex}', file=sys.stderr)
        return 2

    if problems:
        # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
        _hook_output['write_additional_context'](
            _SYNC_CONTEXT.format(problems='; '.join(problems), body=_WRAPPER_BODY.format(name='<name>'))
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
