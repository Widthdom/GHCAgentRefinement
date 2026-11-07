import io
import json
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
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_SHELL_TOOL_NAMES = {'bash', 'powershell'}
# パッチで追加・更新・削除するファイルと移動先。
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$', re.MULTILINE)
# 守るパス。hookの定義・実装・テストと、hookが保持する状態（変更の許可の記録を含む）。
_PROTECTED_PATH_PATTERN = re.compile(r'(?:^|[/\\])(?:\.github[/\\]hooks|ghc-agent-refinement[/\\]hook-states)(?:[/\\]|$)', re.IGNORECASE)
_PROTECTED_PATH_IN_COMMAND_PATTERN = re.compile(r'\.github[/\\]+hooks\b|ghc-agent-refinement[/\\]+hook-states\b', re.IGNORECASE)
# シェルで、ファイルを変更・削除・移動する操作。読み取りやテストの実行（`cat`・`python test-x.py`・`git diff`）は含めない。
_SHELL_MODIFYING_PATTERNS = (
    # 削除・移動・複製・作成・権限変更などのコマンドと、PowerShellのコマンドレット・別名
    re.compile(
        r'(?:^|[\s;|&(`])(?:rm|rmdir|unlink|mv|cp|ln|install|truncate|chmod|chown|touch|tee|dd|shred'
        r'|del|erase|rd|ren|rename|move|copy|xcopy|robocopy'
        r'|Remove-Item|Move-Item|Rename-Item|Copy-Item|New-Item|Set-Content|Add-Content|Clear-Content|Out-File'
        r'|Set-ItemProperty|Tee-Object|ri|mi|rni|cpi|ni|sc|ac|clc)(?=\s|$)',
        re.IGNORECASE,
    ),
    # その場で書き換えるエディター
    re.compile(r'(?:^|[\s;|&(])(?:sed|perl)\s+(?:-\w*i|--in-place)', re.IGNORECASE),
    # 作業ツリーやインデックスを書き換えるgitのサブコマンド
    # `-C <path>`・`-c <name>=<value>` などのオプションとその値は読み飛ばす。
    re.compile(r'\bgit\s+(?:-\S+(?:\s+[^-\s]\S*)?\s+)*(?:checkout|restore|rm|mv|reset|stash|apply|clean|update-index|am|cherry-pick|revert)\b'),
    # 守るパスへのリダイレクト。hookのテストの出力を一時ファイルへ書く場合などは除く。
    re.compile(
        r'>{1,2}\|?\s*["\']?[^\s|;&<>]*(?:\.github[/\\]+hooks|ghc-agent-refinement[/\\]+hook-states)', re.IGNORECASE,
    ),
    # スクリプト内部での書き込み（`python -c`・`node -e`・PowerShellの.NET API）
    re.compile(
        r'\b(?:write_text|write_bytes|writeFile\w*|WriteAll\w*|AppendAll\w*|unlink|rmtree|os\.remove|os\.rename'
        r'|os\.replace|shutil\.(?:move|copy\w*)|fs\.(?:rm|unlink|rename)\w*)\b|open\([^)]*[\'"][wax+]',
    ),
)
_DENY_REASON = (
    'Hook change denied: {targets}. Hooks are the guardrails for this session, and the user has not asked to change '
    'hooks in this session. Do not modify, disable, or delete hook configurations, implementations, definitions, '
    'tests, or hook state files to get past a denial or a check; fix the underlying problem instead. If changing the '
    'hook is genuinely necessary, stop and explain to the user which hook and why, and ask them to request the hook '
    'change explicitly.'
)
_MAX_COMMAND_LENGTH = 200


def _get_edited_paths(tool_name, tool_args):
    if tool_name == 'apply_patch':
        return [match for text in _collect_json_string_values(tool_args) for match in _PATCH_FILE_PATTERN.findall(text)]
    if not isinstance(tool_args, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    return [path] if isinstance(path, str) and path.strip() else []


def _get_targets(tool_name, tool_args, cwd):
    # 戻り値は、拒否の理由で示す変更の対象（ファイルのパスまたはコマンド）のリスト。
    if tool_name in _EDIT_TOOL_NAMES:
        targets = []
        for path in _get_edited_paths(tool_name, tool_args):
            absolute = os.path.normpath(os.path.join(cwd, path))
            if _PROTECTED_PATH_PATTERN.search(absolute):
                display_path = _get_display_path(absolute, cwd)
                if display_path not in targets:
                    targets.append(display_path)
        return targets
    if tool_name in _SHELL_TOOL_NAMES and isinstance(tool_args, dict):
        command = tool_args.get('command')
        if (isinstance(command, str) and _PROTECTED_PATH_IN_COMMAND_PATTERN.search(command)
                and any(pattern.search(command) for pattern in _SHELL_MODIFYING_PATTERNS)):
            if len(command) > _MAX_COMMAND_LENGTH:
                command = command[:_MAX_COMMAND_LENGTH] + '...'
            return [f'command `{command}`']
    return []


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        # 記録側のuserPromptSubmitted hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            _utilities_directory, 'hook-change-request-state', 'hook-change-request-state.py',
        ))

        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        # preToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        targets = _get_targets(
            tool_name.removeprefix('functions.'), _unwrap_tool_args(hook_input['toolArgs']), os.path.normpath(cwd),
        )
        if not targets or state['is_permitted'](state_path):
            return 0
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check hook tampering: {ex}', file=sys.stderr)
        return 2

    reason = _DENY_REASON.format(targets=', '.join(targets))
    print(reason, file=sys.stderr)
    # SDK形式のトップレベルpermissionDecision。正常な拒否なので終了コードは0。
    # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
    print(json.dumps({'permissionDecision': 'deny', 'permissionDecisionReason': reason}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
