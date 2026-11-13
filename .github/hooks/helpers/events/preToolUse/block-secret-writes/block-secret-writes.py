import argparse
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
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_extract_edit_tool_changes = runpy.run_path(os.path.join(
    _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
))['extract_edit_tool_changes']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_read_stored_file_content_for_write_comparison = runpy.run_path(os.path.join(
    _utilities_directory, 'read-stored-file-content-for-write-comparison', 'read-stored-file-content-for-write-comparison.py',
))['read_stored_file_content_for_write_comparison']


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
    # 定義はUTF-8のJSON配列として読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, list) or not definitions:
        raise ValueError('secret definitions must be a non-empty JSON array.')
    compiled = []
    for definition in definitions:
        if not isinstance(definition, dict) or not all(
            isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'pattern')
        ):
            raise ValueError('each secret definition requires non-empty strings for name and pattern.')
        compiled.append((
            definition['name'],
            _build_regex_matchers([definition['pattern']], f"secret definition '{definition['name']}'")[0],
        ))
    return compiled


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
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secret-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input['read_hook_input']()
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

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
            display_paths = ', '.join(_get_display_path(path, real_cwd) for path in resolved_paths) or tool_name.removeprefix('functions.')

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
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
