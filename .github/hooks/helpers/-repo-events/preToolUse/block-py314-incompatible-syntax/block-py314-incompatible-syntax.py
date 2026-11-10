import ast
import io
import json
import os
import runpy
import sys
import warnings
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_simulate_edit_tool_changes = runpy.run_path(os.path.join(
    _utilities_directory, 'simulate-edit-tool-changes', 'simulate-edit-tool-changes.py',
))['simulate_edit_tool_changes']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


# 判定の基準とするPythonのバージョン。これより新しい文法も、古い文法（Python 2の構文など）も拒否する。
_TARGET_VERSION = (3, 14)
# Pythonのソースとして扱う拡張子。
_PYTHON_EXTENSIONS = {'.py', '.pyi', '.pyw'}
# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_MAX_FINDINGS = 3
_FINDING = '{file} line {line}: {message}'
_DENY_REASON = (
    'Edit denied: the resulting Python source would not compile on Python 3.14 ({findings}). This repository '
    'targets Python 3.14, so do not use syntax that 3.14 rejects, such as syntax added in later versions or '
    'removed Python 2 syntax. Fix the code so the whole file compiles on Python 3.14 and retry.'
)


def _is_python_path(path):
    return os.path.splitext(path)[1].lower() in _PYTHON_EXTENSIONS


def _get_syntax_error(source, path):
    # 構文解析に加え、コンパイルで検出される誤り（関数外のreturnなど）も対象とする。警告は対象外。
    try:
        with warnings.catch_warnings(action='ignore'):
            tree = ast.parse(source, path, feature_version=_TARGET_VERSION)
            compile(tree, path, 'exec', dont_inherit=True)
    except SyntaxError as ex:
        return ex
    return None


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        findings = []
        tool_name = tool_name.removeprefix('functions.')
        if tool_name in _EDIT_TOOL_NAMES:
            cwd = os.path.abspath(cwd)
            for path, before, after in _simulate_edit_tool_changes(tool_name, _unwrap_tool_args(tool_args), cwd):
                if not _is_python_path(path):
                    continue
                # 実行中のPythonが基準より古いと、基準の文法を誤って拒否するため判定しない。
                if sys.version_info < _TARGET_VERSION:
                    raise ValueError(f'Python {_TARGET_VERSION[0]}.{_TARGET_VERSION[1]} or later is required.')
                error = _get_syntax_error(after, path)
                if error is None:
                    continue
                # 既にコンパイルできないファイルの編集は、修正途中の場合もあるため拒否しない。
                if before is not None and _get_syntax_error(before, path) is not None:
                    continue
                findings.append(_FINDING.format(
                    file=_get_display_path(path, cwd), line=error.lineno, message=error.msg,
                ))
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check Python 3.14 syntax: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = _DENY_REASON.format(findings='; '.join(findings[:_MAX_FINDINGS]))
        print(reason, file=sys.stderr)
        # SDK形式のトップレベルpermissionDecision。正常な拒否なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({
            'permissionDecision': 'deny',
            'permissionDecisionReason': reason,
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
