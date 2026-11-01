import argparse
import io
import json
import os
import re
import sys
from typing import cast


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
# 置換前の内容を表す引数。書き込む内容ではないため検査せず、既存の値の照合に使う。
REMOVED_TEXT_KEYS = {'old_str', 'old_string'}
# 書き込む内容ではない引数。
IGNORED_KEYS = {'path', 'command'}
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:(?:Add|Update) File|Move to): (.+?)\s*$', re.MULTILINE)
# `.env` と `.env.local` などは対象とし、値を含まないひな形は対象外とする。
ENV_FILE_PATTERN = re.compile(r'^\.env(?:\..+)?$', re.IGNORECASE)
ENV_TEMPLATE_SUFFIXES = ('.example', '.sample', '.template', '.dist')
# 照合した値のうち、末尾がこの長さ以上の同じ文字の連続（xxxx…、0000…）ならプレースホルダーとみなす。
PLACEHOLDER_RUN_LENGTH = 8
DENY_REASON = (
    'Write denied: {findings}. Do not write credentials into files, because files can be committed, shared, '
    'or sent to external services. Read the value from an environment variable or a secret store, '
    'or write a placeholder instead. If the user explicitly wants this value in the file, '
    'tell the user so that they can make the edit themselves. Do not repeat the value in your response.'
)


def load_definitions(path):
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
        try:
            compiled.append((definition['name'], re.compile(definition['pattern'])))
        except re.PatternError as ex:
            raise ValueError(f"invalid regular expression in secret definition '{definition['name']}': {ex}") from None
    return compiled


def get_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列（apply_patch）で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def collect_strings(value, ignored_keys=frozenset()):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in ignored_keys:
                yield from collect_strings(item, ignored_keys)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item, ignored_keys)


def split_patch(text):
    # パッチの追加行（`+`）を書き込む内容、削除行（`-`）を既存の内容として分ける。
    added = []
    removed = []
    for line in text.splitlines():
        if line.startswith('+'):
            added.append(line[1:])
        elif line.startswith('-'):
            removed.append(line[1:])
    return '\n'.join(added), '\n'.join(removed)


def get_write(tool_name, tool_args):
    # 戻り値は (書き込む内容, 置換前の内容, 対象パスのリスト)。
    if tool_name == 'apply_patch':
        texts = list(collect_strings(tool_args))
        parts = [split_patch(text) for text in texts]
        paths = [match for text in texts for match in PATCH_FILE_PATTERN.findall(text)]
        return '\n'.join(added for added, _ in parts), '\n'.join(removed for _, removed in parts), paths
    if not isinstance(tool_args, dict):
        return '', '', []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return '', '', []
    written = '\n'.join(collect_strings(tool_args, IGNORED_KEYS | REMOVED_TEXT_KEYS))
    removed = '\n'.join(text for key in REMOVED_TEXT_KEYS for text in collect_strings(tool_args.get(key)))
    path = tool_args.get('path')
    return written, removed, [path] if isinstance(path, str) and path.strip() else []


def is_env_file(path):
    name = os.path.basename(path).lower()
    return bool(ENV_FILE_PATTERN.match(name)) and not name.endswith(ENV_TEMPLATE_SUFFIXES)


def is_placeholder(value):
    return len(value) >= PLACEHOLDER_RUN_LENGTH and len(set(value[-PLACEHOLDER_RUN_LENGTH:])) == 1


def read_existing(path):
    # 既に書かれている値を再び書くのは新たな漏えいではないため、照合用に現在の内容を読む。
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()
    except OSError:
        return ''


def find_secret_names(definitions, written, existing):
    names = []
    for name, pattern in definitions:
        if any(
            not is_placeholder(match.group(0)) and match.group(0) not in existing
            for match in pattern.finditer(written)
        ):
            names.append(name)
    return names


def get_display_path(path, cwd):
    # cwd配下のファイルは相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd)
    except ValueError:
        pass
    return path


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secret-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = load_definitions(parsed_arguments.definitions_path)

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
        # preToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        normalized_cwd = os.path.normcase(os.path.realpath(cwd))
        findings = []
        tool_name = tool_name.removeprefix('functions.')
        if tool_name in EDIT_TOOL_NAMES:
            written, removed, paths = get_write(tool_name, get_tool_args(hook_input['toolArgs']))
            resolved_paths = sorted({os.path.normcase(os.path.realpath(os.path.join(cwd, path))) for path in paths})
            display_paths = ', '.join(get_display_path(path, normalized_cwd) for path in resolved_paths) or tool_name

            env_paths = [path for path in resolved_paths if is_env_file(path)]
            if env_paths:
                files = ', '.join(get_display_path(path, normalized_cwd) for path in env_paths)
                findings.append(f'{files} is an environment file that usually holds secrets')

            existing = '\n'.join([removed, *(read_existing(path) for path in resolved_paths if os.path.isfile(path))])
            names = find_secret_names(definitions, written, existing)
            if names:
                findings.append(f'the content for {display_paths} looks like it contains a secret ({", ".join(names)})')
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check secret writes: {ex}', file=sys.stderr)
        return 2

    if findings:
        reason = DENY_REASON.format(findings='; '.join(findings))
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
