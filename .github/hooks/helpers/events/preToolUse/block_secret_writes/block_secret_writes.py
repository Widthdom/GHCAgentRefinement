import argparse
import io
import os
import re
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'parse_tool_calls'),
    os.path.join(_utilities_directory, 'write_hook_output'),
    os.path.join(_utilities_directory, 'read_definitions'),
    os.path.join(_utilities_directory, 'edit_tool_changes'),
    os.path.join(_utilities_directory, 'get_display_path'),
    os.path.join(_utilities_directory, 'read_stored_file_content_for_write_comparison'),
]
import read_hook_input as _hook_input
import parse_tool_calls as _tool_calls
import write_hook_output as _hook_output
import read_definitions as _definitions
from edit_tool_changes import extract_edit_tool_changes as _extract_edit_tool_changes
from get_display_path import get_display_path as _get_display_path
from read_stored_file_content_for_write_comparison import read_stored_file_content_for_write_comparison as _read_stored_file_content_for_write_comparison


# `.env` と `.env.local` などは対象とし、値を含まないひな形は対象外とする。
_ENV_FILE_PATTERN = re.compile(r'^\.env(?:\..+)?$', re.IGNORECASE)
_ENV_TEMPLATE_SUFFIXES = ('.example', '.sample', '.template', '.dist')
# 照合した値のうち、末尾がこの長さ以上の同じ文字の連続（xxxx…、0000…）ならプレースホルダーとみなす。
_PLACEHOLDER_RUN_LENGTH = 8
_DENY_REASON = (
    'Write denied: {findings}. Do not write credentials into files, because files can be committed, shared, '
    'or sent to external services. Read the value from an environment variable or a secret store, '
    'or write a placeholder instead. If the user explicitly wants this value in the file, '
    'tell the user so that they can make the edit themselves. Do not repeat the value in your response.'
)


def _load_definitions(path):
    # 戻り値は (名前, コンパイル済みの正規表現) のリスト。
    label = 'secret definitions'
    return _definitions.build_named_regex_matchers(_definitions.read_definitions(path, label, list), label)


def _is_env_file(path):
    name = os.path.basename(path).lower()
    return bool(_ENV_FILE_PATTERN.match(name)) and not name.endswith(_ENV_TEMPLATE_SUFFIXES)


def _is_placeholder(value):
    return len(value) >= _PLACEHOLDER_RUN_LENGTH and len(set(value[-_PLACEHOLDER_RUN_LENGTH:])) == 1


def _find_secret_names(definitions, written, existing):
    names = []
    for name, pattern in definitions:
        if any(
            not _is_placeholder(match.group(0)) and match.group(0) not in existing
            for match in pattern.finditer(written)
        ):
            names.append(name)
    return names


def main(arguments=None):
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secret_definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input.read_hook_input()
        cwd = _hook_input.get_cwd(hook_input)
        tool_name, tool_args = _hook_input.get_tool_call(hook_input)

        real_cwd = os.path.realpath(cwd)
        findings = []
        # 置換前の内容は書き込む内容ではないため検査せず、既存の値の照合に使う。
        changes = _extract_edit_tool_changes(tool_name, tool_args)
        if changes:
            written = '\n'.join(change['written'] for change in changes)
            removed = '\n'.join(change['removed'] for change in changes)
            paths = [path for change in changes for path in (change['path'], change['moved_to']) if path]
            # 同じファイルの別表記はnormcaseで1つにまとめ、表示には正規化前のパスを使う。
            resolved = {}
            for path in paths:
                real_path = os.path.realpath(os.path.join(cwd, path))
                resolved.setdefault(os.path.normcase(real_path), real_path)
            resolved_paths = [resolved[key] for key in sorted(resolved)]
            display_paths = ', '.join(_get_display_path(path, real_cwd) for path in resolved_paths) or _tool_calls.get_tool_name(tool_name)

            env_paths = [path for path in resolved_paths if _is_env_file(path)]
            if env_paths:
                files = ', '.join(_get_display_path(path, real_cwd) for path in env_paths)
                findings.append(f'{files} is an environment file that usually holds secrets')

            # 既に書かれている値を再び書くのは新たな漏えいではないため、照合用に現在の内容を読む。
            existing = '\n'.join([
                removed, *(_read_stored_file_content_for_write_comparison(path) or '' for path in resolved_paths if os.path.isfile(path)),
            ])
            names = _find_secret_names(definitions, written, existing)
            if names:
                findings.append(f'the content for {display_paths} looks like it contains a secret ({", ".join(names)})')
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check secret writes: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = _DENY_REASON.format(findings='; '.join(findings))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output.write_deny_decision(reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
