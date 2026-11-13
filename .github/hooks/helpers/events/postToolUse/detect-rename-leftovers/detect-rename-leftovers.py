import io
import os
import re
import runpy
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))
_extract_edit_tool_changes = runpy.run_path(os.path.join(
    _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
))['extract_edit_tool_changes']


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'str_replace_editor', 'apply_patch'}
# 他のファイルから参照される定義。ローカル変数の名前の変更は、他のファイルに参照が残らないため対象外とする。
_DEFINITION_PATTERNS = [
    re.compile(r'\b(?:class|interface|struct|enum|record|trait|protocol|type)\s+(?P<name>[A-Za-z_]\w*)'),
    re.compile(r'^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)'),
    re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)'),
    re.compile(r'^\s*export\s+(?:default\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)'),
    re.compile(r'^func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)'),
    re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+(?P<name>[A-Za-z_]\w*)'),
    # C#・Javaなどの修飾子付きのメソッド・プロパティ・フィールド。
    # 修飾子の `new`（メンバーの隠蔽）と、行頭のインスタンスの生成（`new Foo(`・`new Foo<T> {`）を区別する。
    re.compile(
        r'^\s*(?:\[[^\]]*\]\s*)*'
        r'(?!new\s+[A-Za-z_][\w.]*\s*(?:<[^=;(]*>\s*)?[({\[])'
        r'(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed|readonly|extern|new|'
        r'partial|final|synchronized|const|required)\s+)+'
        r'(?:[\w<>\[\],.?]+(?:\s*<[^=;(]*>)?\s+)?(?P<name>[A-Za-z_]\w*)\s*(?:<[^=;(]*>\s*)?(?:\(|\{|=>|=|;)'
    ),
    # Pythonのモジュール直下の定数。
    re.compile(r'^(?P<name>[A-Z][A-Z0-9_]+)\s*(?::[^=]+)?=(?!=)'),
]
_MIN_NAME_LENGTH = 3
_MAX_LOCATIONS = 10
_GIT_TIMEOUT_SECONDS = 3
# gitの実行全体の期限。名前が多くても、hookの `timeoutSec`（10秒）より前に打ち切る。
_GIT_DEADLINE = _git['create_deadline'](7)
_LEFTOVER_CONTEXT = (
    'You renamed {renames}, but the old names still appear in: {locations}. '
    'Update the remaining references, including strings, configuration, and documentation, '
    'or confirm that each one intentionally refers to something else.'
)


def _get_changes(tool_name, tool_args):
    # 戻り値は {path, removed（置換前の内容）, written（置換後の内容）} のリスト。
    # 置換前の内容が分からない上書き（create）と挿入（insert）は、名前の変更を判断できないため対象外とする。
    changes = []
    for change in _extract_edit_tool_changes(tool_name, tool_args):
        if change['patch_lines'] is not None:
            changes.append({'path': change['path'], 'removed': change['removed'], 'written': change['written']})
        elif change['replacement'] is not None:
            removed, written = change['replacement']
            changes.append({'path': change['path'], 'removed': removed, 'written': written})
    return changes


def _get_defined_names(text):
    names = []
    for line in text.splitlines():
        for pattern in _DEFINITION_PATTERNS:
            match = pattern.search(line)
            if match:
                name = match.group('name')
                if len(name) >= _MIN_NAME_LENGTH and name not in names:
                    names.append(name)
                break
    return names


def _contains_word(text, name):
    return re.search(rf'(?<![\w$]){re.escape(name)}(?![\w$])', text) is not None


def _find_renames(removed, written):
    # 置換前だけにある定義と置換後だけにある定義を、現れた順に組にする。
    # 置換後に古い名前が残る定義と、置換前に新しい名前がある定義は、名前の変更ではないため除く。
    old_names = [name for name in _get_defined_names(removed) if not _contains_word(written, name)]
    new_names = [name for name in _get_defined_names(written) if not _contains_word(removed, name)]
    if not old_names or len(old_names) != len(new_names):
        return []
    return list(zip(old_names, new_names))


def _find_locations(root, name):
    # git管理下と、無視されていない未追跡のファイルから、単語として一致する行を探す。
    # git grepは一致がなければ終了コード1、エラーなら2以上を返すため、どちらも一致なしとして扱う。
    output = _git['get_git_output'](
        root, ['grep', '-n', '-I', '-w', '-F', '--untracked', '-e', name, '--'], _GIT_TIMEOUT_SECONDS, _GIT_DEADLINE,
    )
    if output is None:
        return []
    locations = []
    for line in output.splitlines():
        path, _, rest = line.partition(':')
        line_number, _, _ = rest.partition(':')
        if path and line_number.isdigit():
            locations.append(f'{path}:{line_number}')
    return locations


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)
    except (UnicodeError, ValueError) as ex:
        print(f'Failed to detect rename leftovers: {ex}', file=sys.stderr)
        return 2

    tool_name = tool_name.removeprefix('functions.')
    if tool_name not in _EDIT_TOOL_NAMES:
        return 0
    renames = []
    for change in _get_changes(tool_name, tool_args):
        for rename in _find_renames(change['removed'], change['written']):
            if rename not in renames:
                renames.append(rename)
    if not renames:
        return 0
    # gitがない、またはリポジトリ外では探せないため何もしない。
    root = _git['get_repository_root'](cwd, _GIT_TIMEOUT_SECONDS, _GIT_DEADLINE)
    if root is None:
        return 0

    found = []
    locations = []
    for old_name, new_name in renames:
        name_locations = _find_locations(root, old_name)
        if name_locations:
            found.append(f'`{old_name}` to `{new_name}`')
            locations.extend(f'{location} (`{old_name}`)' for location in name_locations)
    if not locations:
        return 0
    shown = ', '.join(locations[:_MAX_LOCATIONS])
    if len(locations) > _MAX_LOCATIONS:
        shown += f', and {len(locations) - _MAX_LOCATIONS} more'
    # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
    _hook_output['write_additional_context'](_LEFTOVER_CONTEXT.format(renames=', '.join(found), locations=shown))
    return 0


if __name__ == '__main__':
    sys.exit(main())
