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
_get_edited_paths = runpy.run_path(os.path.join(_utilities_directory, 'get-edited-paths', 'get-edited-paths.py'))['get_edited_paths']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_SHELL_TOOL_NAMES = {'bash', 'powershell'}
# 守るパス。hookの定義・実装・テストと、hookが保持する状態（変更の許可の記録を含む）。
_PROTECTED_PATH_PATTERN = re.compile(r'(?:^|[/\\])(?:\.github[/\\]hooks|ghc-agent-refinement[/\\]hook-states)(?:[/\\]|$)', re.IGNORECASE)
_PROTECTED_PATH_IN_COMMAND_PATTERN = re.compile(r'\.github[/\\]+hooks\b|ghc-agent-refinement[/\\]+hook-states\b', re.IGNORECASE)
# 守るパスを含むフォルダーそのもの（`.github`・`.github/*` など）。丸ごと削除・移動すると守るパスも変わる。
_PARENT_PATH_IN_COMMAND_PATTERN = re.compile(r'(?<![\w.-])(?:\.github|ghc-agent-refinement)[/\\]*\*?(?![\w./\\-])', re.IGNORECASE)
# シェルのコマンドの区切り。`2>&1`・`&>` などのリダイレクトの `&` は区切りとみなさない。
_LIST_SEPARATOR_PATTERN = re.compile(r'&&|\|\||(?<![<>])&(?!>)|[;\n]')
_PIPE_SEPARATOR_PATTERN = re.compile(r'\|')
# 単純コマンドの始まり。単純コマンドの先頭のほか、途中の `(`・`{`・`$(`・`` ` ``（サブシェル、PowerShellのスクリプトブロック、
# コマンド置換）の後も含む。引用符、予約語、前置きのコマンドとそのオプション（`timeout` は時間指定も）、変数の代入を読み飛ばす。
# `bash -c "…"`・`pwsh -Command "…"` の引用符の中もコマンドの始まりとみなす。
_COMMAND_START = (
    r'(?:^|\s-(?:c|Command)\s+|[({`]|\$\()\s*(?:[({`!"\']|\$\()*\s*'
    r'(?:(?:then|do|else|elif|if|while|until|time|sudo|env|command|builtin|exec|eval|nohup|nice|xargs)(?:\s+-\S+)*\s+'
    r'|timeout(?:\s+-\S+)*\s+\S+\s+'
    r'|[A-Za-z_]\w*=\S*\s+)*'
)
# 守るパスを対象に繰り返すループ（`for f in .github/hooks/*; do rm $f; done`）。以降の単純コマンドも守るパスを扱うとみなす。
_LOOP_PATTERN = re.compile(r'^\s*(?:for|foreach|select|while|until)\b', re.IGNORECASE)
# 親のフォルダーへ戻るだけの移動先（`..`・`../..`）。守るパスの中から戻っても、守るパスを含むフォルダーにいる。
_PARENT_ONLY_PATTERN = re.compile(r'^["\']?\.\.(?:[/\\]\.\.)*[/\\]?["\']?\s*$')
# 書き込みを含む `open` のモードの文字列。シェルの引用符の中で `\"w\"` とエスケープした場合も含む。
_WRITE_MODE = r'\\?[\'"][rbtU]*[wax+][rwaxbtU+]*\\?[\'"]'
_XARGS_PATTERN = re.compile(r'\bxargs\b', re.IGNORECASE)
# 引数に書いた対象を変更するコマンド。守るパスが同じ単純コマンドにあれば変更とみなす。
# `tee`・`Out-File`・`Tee-Object` はパイプの入力を書き込む内容として受け取るため、ここに含める。
_ARGUMENT_TARGET_COMMANDS = (
    r'rm|rmdir|unlink|mv|cp|ln|install|truncate|chmod|chown|touch|tee|dd|shred'
    r'|del|erase|rd|ren|rename|move|copy|xcopy|robocopy|Out-File|Tee-Object'
)
# パイプの入力から対象のパスを受け取れるPowerShellのコマンドレット。守るパスが同じパイプラインにあれば変更とみなす。
_PIPELINE_TARGET_COMMANDS = (
    r'Remove-Item|Move-Item|Rename-Item|Copy-Item|New-Item|Set-Content|Add-Content|Clear-Content|Set-ItemProperty'
)
# PowerShellの別名。bashでは別のコマンドや引数と重なるため、powershellのときだけ照合する。
_POWERSHELL_ALIASES = r'ri|mi|rni|cpi|ni|sc|ac|clc'
_ARGUMENT_TARGET_PATTERN = re.compile(_COMMAND_START + rf'(?:{_ARGUMENT_TARGET_COMMANDS})(?:\.exe)?(?=\s|$)', re.IGNORECASE)
_PIPELINE_TARGET_PATTERN = re.compile(_COMMAND_START + rf'(?:{_PIPELINE_TARGET_COMMANDS})(?=\s|$)', re.IGNORECASE)
_POWERSHELL_ALIAS_PATTERN = re.compile(_COMMAND_START + rf'(?:{_POWERSHELL_ALIASES})(?=\s|$)', re.IGNORECASE)
_SEGMENT_MODIFYING_PATTERNS = (
    # その場で書き換えるエディター
    re.compile(_COMMAND_START + r'(?:sed|perl)\s+(?:-\w*i|--in-place)', re.IGNORECASE),
    # `find` の削除と、見つけたファイルへの変更系コマンドの実行
    re.compile(rf'\s-delete\b|\s-exec(?:dir)?\s+(?:{_ARGUMENT_TARGET_COMMANDS}|sed|perl)(?=\s)', re.IGNORECASE),
    # 作業ツリーやインデックスを書き換えるgitのサブコマンド
    # `-C <path>`・`-c <name>=<value>` などのオプションとその値は読み飛ばす。
    re.compile(
        _COMMAND_START + r'git\s+(?:-\S+(?:\s+[^-\s]\S*)?\s+)*'
        r'(?:checkout|restore|rm|mv|reset|stash|apply|clean|update-index|am|cherry-pick|revert)\b',
    ),
)
_CHANGE_DIRECTORY_PATTERN = re.compile(_COMMAND_START + r'(?:cd|pushd|chdir|Set-Location|sl)\s+(.+)', re.IGNORECASE)
# 移動先のフォルダーの中での、相対パスへのリダイレクト。`/dev/null`・絶対パス・変数（一時フォルダーなど）への書き込みは除く。
_RELATIVE_REDIRECT_PATTERN = re.compile(
    r'(?<![<&\d])\d?>{1,2}\|?\s*["\']?(?![&/~$\\]|[A-Za-z]:|nul\b)[^\s|;&<>"\']', re.IGNORECASE,
)
# コマンドのどこに書かれていても変更とみなす操作。
_COMMAND_MODIFYING_PATTERNS = (
    # 守るパスへのリダイレクト。hookのテストの出力を一時ファイルへ書く場合などは除く。
    re.compile(
        r'>{1,2}\|?\s*["\']?[^\s|;&<>]*(?:\.github[/\\]+hooks|ghc-agent-refinement[/\\]+hook-states)', re.IGNORECASE,
    ),
    # スクリプト内部での書き込み（`python -c`・`node -e`・PowerShellの.NET API）
    # `open` は、2番目の引数か `mode=` が書き込みを含むモードの場合だけ書き込みとみなす（`Path.open` はモードが最初の引数）。
    # `encoding='ascii'` などの文字列や、読むだけの `open(path)` は書き込みとみなさない。
    re.compile(
        r'\b(?:write_text|write_bytes|writeFile\w*|WriteAll\w*|AppendAll\w*|rmtree|os\.remove|os\.rename'
        r'|os\.replace|shutil\.(?:move|copy\w*)|fs\.(?:rm|unlink|rename)\w*)\b|\.unlink\('
        r'|\bopen\([^,()]*(?:\([^()]*\)[^,()]*)*,\s*' + _WRITE_MODE
        + r'|\bopen\([^)]*\bmode\s*=\s*' + _WRITE_MODE
        + r'|\.open\(\s*' + _WRITE_MODE,
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


def _mentions_protected_path(text):
    return bool(_PROTECTED_PATH_IN_COMMAND_PATTERN.search(text) or _PARENT_PATH_IN_COMMAND_PATTERN.search(text))


def _is_modifying_command(tool_name, command):
    # 守るパスへの変更を含むかを、`;`・`&&` などで区切った単純コマンドごとに判定する。
    # 守るパスは、引数を対象にするコマンドでは同じ単純コマンドに、パイプの入力を対象にできるコマンドでは同じパイプラインにあることを求める。
    # `cd .github` など守るパスへ移動した後は、パスを書かない変更も対象にする。
    if any(pattern.search(command) for pattern in _COMMAND_MODIFYING_PATTERNS):
        return True
    in_protected_directory = False
    in_protected_loop = False
    for pipeline in _LIST_SEPARATOR_PATTERN.split(command):
        if _LOOP_PATTERN.match(pipeline) and _mentions_protected_path(pipeline):
            in_protected_loop = True
        segments = _PIPE_SEPARATOR_PATTERN.split(pipeline)
        pipeline_has_path = in_protected_directory or in_protected_loop or _mentions_protected_path(pipeline)
        for segment in segments:
            change_directory = _CHANGE_DIRECTORY_PATTERN.search(segment)
            if change_directory:
                target = change_directory.group(1)
                in_protected_directory = _mentions_protected_path(target) or (
                    in_protected_directory and bool(_PARENT_ONLY_PATTERN.match(target))
                )
                continue
            segment_has_path = in_protected_directory or in_protected_loop or _mentions_protected_path(segment)
            argument_target = _ARGUMENT_TARGET_PATTERN.search(segment)
            if argument_target and (
                    segment_has_path or (pipeline_has_path and _XARGS_PATTERN.search(argument_target.group(0)))):
                return True
            if pipeline_has_path and (
                    _PIPELINE_TARGET_PATTERN.search(segment)
                    or (tool_name == 'powershell' and _POWERSHELL_ALIAS_PATTERN.search(segment))):
                return True
            if segment_has_path and any(pattern.search(segment) for pattern in _SEGMENT_MODIFYING_PATTERNS):
                return True
            if in_protected_directory and _RELATIVE_REDIRECT_PATTERN.search(segment):
                return True
    return False


def _get_targets(tool_name, tool_args, cwd):
    # 戻り値は、拒否の理由で示す変更の対象（ファイルのパスまたはコマンド）のリスト。
    if tool_name in _EDIT_TOOL_NAMES:
        targets = []
        for path in _get_edited_paths(tool_name, tool_args):
            absolute = os.path.normpath(os.path.join(cwd, path))
            # シンボリックリンク経由のパスも、リンク先が守るパスなら対象にする。
            if _PROTECTED_PATH_PATTERN.search(absolute) or _PROTECTED_PATH_PATTERN.search(os.path.realpath(absolute)):
                display_path = _get_display_path(absolute, cwd)
                if display_path not in targets:
                    targets.append(display_path)
        return targets
    if tool_name in _SHELL_TOOL_NAMES and isinstance(tool_args, dict):
        command = tool_args.get('command')
        if (isinstance(command, str) and _mentions_protected_path(command)
                and _is_modifying_command(tool_name, command)):
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

        hook_input = _hook_input['read_hook_input']()
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        targets = _get_targets(
            tool_name.removeprefix('functions.'), _unwrap_tool_args(tool_args), os.path.normpath(cwd),
        )
        if not targets or state['is_permitted'](state_path):
            return 0
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check hook tampering: {ex}', file=sys.stderr)
        return 2

    reason = _DENY_REASON.format(targets=', '.join(targets))
    print(reason, file=sys.stderr)
    # 正常な拒否なので終了コードは0。
    _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
