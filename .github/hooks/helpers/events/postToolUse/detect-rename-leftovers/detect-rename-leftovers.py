import io
import json
import os
import re
import runpy
import subprocess
import sys
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


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'str_replace_editor', 'apply_patch'}
_REMOVED_TEXT_KEYS = ('old_str', 'old_string')
_WRITTEN_TEXT_KEYS = ('new_str', 'new_string')
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+?)\s*$')
# 他のファイルから参照される定義。ローカル変数の名前の変更は、他のファイルに参照が残らないため対象外とする。
_DEFINITION_PATTERNS = [
    re.compile(r'\b(?:class|interface|struct|enum|record|trait|protocol|type)\s+(?P<name>[A-Za-z_]\w*)'),
    re.compile(r'^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)'),
    re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)'),
    re.compile(r'^\s*export\s+(?:default\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)'),
    re.compile(r'^func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)'),
    re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+(?P<name>[A-Za-z_]\w*)'),
    # C#・Javaなどの修飾子付きのメソッド・プロパティ・フィールド。
    re.compile(
        r'^\s*(?:\[[^\]]*\]\s*)*'
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
_LEFTOVER_CONTEXT = (
    'You renamed {renames}, but the old names still appear in: {locations}. '
    'Update the remaining references, including strings, configuration, and documentation, '
    'or confirm that each one intentionally refers to something else.'
)


def _get_patch_changes(text):
    # パッチをファイルごとに分け、削除行（`-`）と追加行（`+`）の組にする。
    changes = []
    for line in text.splitlines():
        match = _PATCH_FILE_PATTERN.match(line)
        if match:
            changes.append({'path': match.group(2), 'removed': [], 'written': []})
        elif changes and line.startswith('-'):
            changes[-1]['removed'].append(line[1:])
        elif changes and line.startswith('+'):
            changes[-1]['written'].append(line[1:])
    return [
        {'path': change['path'], 'removed': '\n'.join(change['removed']), 'written': '\n'.join(change['written'])}
        for change in changes
    ]


def _get_changes(tool_name, tool_args):
    # 戻り値は {path, removed（置換前の内容）, written（置換後の内容）} のリスト。
    # 置換前の内容が分からない上書き（create）と挿入（insert）は、名前の変更を判断できないため対象外とする。
    if tool_name == 'apply_patch':
        return [change for text in _collect_json_string_values(tool_args) for change in _get_patch_changes(text)]
    if not isinstance(tool_args, dict):
        return []
    if tool_name == 'str_replace_editor' and tool_args.get('command') != 'str_replace':
        return []
    path = tool_args.get('path')
    removed = next((tool_args[key] for key in _REMOVED_TEXT_KEYS if isinstance(tool_args.get(key), str)), None)
    written = next((tool_args[key] for key in _WRITTEN_TEXT_KEYS if isinstance(tool_args.get(key), str)), None)
    if not isinstance(path, str) or not path.strip() or removed is None or written is None:
        return []
    return [{'path': path, 'removed': removed, 'written': written}]


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


def _get_repository_root(cwd):
    try:
        result = subprocess.run(
            ['git', '-C', cwd, 'rev-parse', '--show-toplevel'],
            capture_output=True, timeout=_GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode('utf-8', errors='replace').strip() or None


def _find_locations(root, name):
    # git管理下と、無視されていない未追跡のファイルから、単語として一致する行を探す。
    try:
        result = subprocess.run(
            ['git', '-C', root, 'grep', '-n', '-I', '-w', '-F', '--untracked', '-e', name, '--'],
            capture_output=True, timeout=_GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    # git grepは一致がなければ終了コード1、エラーなら2以上を返す。
    if result.returncode != 0:
        return []
    locations = []
    for line in result.stdout.decode('utf-8', errors='replace').splitlines():
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
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd must be a non-empty string.')
        if not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        # postToolUseには、呼び出したツール1件の名前（toolName）と引数（toolArgs）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')
    except (UnicodeError, ValueError) as ex:
        print(f'Failed to detect rename leftovers: {ex}', file=sys.stderr)
        return 2

    tool_name = tool_name.removeprefix('functions.')
    if tool_name not in _EDIT_TOOL_NAMES:
        return 0
    renames = []
    for change in _get_changes(tool_name, _unwrap_tool_args(hook_input['toolArgs'])):
        for rename in _find_renames(change['removed'], change['written']):
            if rename not in renames:
                renames.append(rename)
    if not renames:
        return 0
    # gitがない、またはリポジトリ外では探せないため何もしない。
    root = _get_repository_root(cwd)
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
    # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
    print(json.dumps({
        'additionalContext': _LEFTOVER_CONTEXT.format(renames=', '.join(found), locations=shown),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
