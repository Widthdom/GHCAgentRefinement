import io
import json
import os
import re
import runpy
import sys
import unicodedata
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']
_edited_paths = runpy.run_path(os.path.join(_utilities_directory, 'get-edited-paths', 'get-edited-paths.py'))


_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+)$')
_INVALID_CHARACTERS = frozenset('<>:"\\|?*')
# Windows also reserves superscript 1, 2 and 3 in COM/LPT device names.
_DEVICE_DIGITS = '123456789' + ''.join(chr(value) for value in (0xB9, 0xB2, 0xB3))
_RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    prefix + digit for prefix in ('COM', 'LPT') for digit in _DEVICE_DIGITS
}


def _path_key(path):
    return unicodedata.normalize('NFC', path).casefold()


def _invalid_name_reason(name):
    if name.endswith((' ', '.')):
        return 'a name ends with a space or dot'
    if any(character in _INVALID_CHARACTERS or ord(character) < 32 for character in name):
        return 'a name contains a character forbidden on Windows'
    if name.split('.')[0].rstrip(' ').upper() in _RESERVED_NAMES:
        return 'a name is reserved by Windows'
    return None


def _get_operations(tool_name, tool_args):
    destinations = []
    removed = set()
    if tool_name == 'apply_patch':
        for text in _collect_json_string_values(tool_args):
            operation = None
            source = None
            for line in text.split('\n'):
                line = line.removesuffix('\r')
                match = _PATCH_FILE_PATTERN.fullmatch(line)
                if match:
                    operation, source = match.groups()
                    if operation == 'Add':
                        destinations.append(source)
                    elif operation == 'Delete':
                        removed.add(source)
                elif line.startswith('*** Move to: ') and operation == 'Update':
                    destinations.append(line[len('*** Move to: '):])
                    removed.add(source)
    else:
        if not isinstance(tool_args, dict):
            raise ValueError('editing toolArgs must be a JSON object.')
        if tool_name == 'str_replace_editor' and tool_args.get('command') in ('view', 'undo_edit'):
            return [], set()
        # 対象のファイルのパスは `path`、`create` では `file_path` で渡される。引数名はget-edited-paths utilityと共有する。
        path = _edited_paths['get_path_argument'](tool_args)
        if path is None:
            raise ValueError(f"one of {', '.join(_edited_paths['PATH_KEYS'])} must be a non-empty string.")
        destinations.append(path)
    if any(not path or '\0' in path for path in [*destinations, *removed]):
        raise ValueError('file paths must be non-empty and must not contain NUL.')
    return destinations, removed


def _absolute_path(cwd, path):
    # Keep trailing dots/spaces intact instead of letting Windows full-path APIs trim them.
    joined = os.path.join(os.path.abspath(cwd), path)
    if not os.path.isabs(joined):
        raise ValueError('file paths must resolve to absolute paths without drive-relative syntax.')
    return os.path.normpath(joined).replace(os.sep, '/')


def _check_paths(cwd, destinations, removed):
    removed_paths = {_absolute_path(cwd, path) for path in removed}
    planned = {}
    directory_entries = {}
    findings = []
    for destination in destinations:
        path = _absolute_path(cwd, destination)
        drive, tail = os.path.splitdrive(path)
        parent = drive + '/'
        parts = tail.lstrip('/').split('/')
        for name in parts:
            child = parent.rstrip('/') + '/' + name
            if parent not in directory_entries:
                try:
                    directory_entries[parent] = os.listdir(parent)
                except (FileNotFoundError, NotADirectoryError):
                    directory_entries[parent] = []
            entries = [
                entry for entry in directory_entries[parent]
                if parent.rstrip('/') + '/' + entry not in removed_paths
            ]
            key = _path_key(child)
            # Existing names may be edited without introducing a new portability problem.
            if name not in entries:
                reason = _invalid_name_reason(name)
                if reason:
                    findings.append(f'{destination!r}: {reason} ({name!r})')
                    break
                collision = next((entry for entry in entries if _path_key(entry) == _path_key(name)), None)
                if collision is not None:
                    findings.append(f'{destination!r}: name {name!r} collides with existing name {collision!r}')
                    break
            previous = planned.get(key)
            if previous is not None and previous != child:
                findings.append(f'{destination!r}: collides with another destination {previous!r}')
                break
            planned[key] = child
            parent = child
    return findings


def main():
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    try:
        hook_input = _hook_input['read_hook_input']()
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)
        tool_name = tool_name.removeprefix('functions.')
        if tool_name not in _EDIT_TOOL_NAMES:
            return 0
        tool_args = _unwrap_tool_args(tool_args)
        if tool_name == 'apply_patch' and not isinstance(tool_args, (str, dict)):
            raise ValueError('apply_patch toolArgs must be a patch string or JSON object.')
        destinations, removed = _get_operations(tool_name, tool_args)
        findings = _check_paths(cwd, destinations, removed)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check portable paths: {ex}', file=sys.stderr)
        return 2
    if findings:
        print(json.dumps({
            'permissionDecision': 'deny',
            'permissionDecisionReason': (
                'Edit denied: nonportable destination paths: ' + '; '.join(findings[:10]) + '. '
                'Choose names usable on Windows, macOS and Linux, without case or Unicode-normalization '
                'collisions. A case-only file rename is allowed when its source is removed in the same patch.'
            ),
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
