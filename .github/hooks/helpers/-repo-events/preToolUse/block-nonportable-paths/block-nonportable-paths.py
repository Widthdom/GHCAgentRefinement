import io
import json
import os
import re
import runpy
import sys
import unicodedata
from typing import cast


EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+)$')
INVALID_CHARACTERS = frozenset('<>:"\\|?*')
# Windows also reserves superscript 1, 2 and 3 in COM/LPT device names.
DEVICE_DIGITS = '123456789' + ''.join(chr(value) for value in (0xB9, 0xB2, 0xB3))
RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    prefix + digit for prefix in ('COM', 'LPT') for digit in DEVICE_DIGITS
}


def path_key(path):
    return unicodedata.normalize('NFC', path).casefold()


def invalid_name_reason(name):
    if name.endswith((' ', '.')):
        return 'a name ends with a space or dot'
    if any(character in INVALID_CHARACTERS or ord(character) < 32 for character in name):
        return 'a name contains a character forbidden on Windows'
    if name.split('.')[0].rstrip(' ').upper() in RESERVED_NAMES:
        return 'a name is reserved by Windows'
    return None


def get_operations(tool_name, tool_args, helpers):
    destinations = []
    removed = set()
    if tool_name == 'apply_patch':
        for text in helpers['collect_strings'](tool_args):
            operation = None
            source = None
            for line in text.split('\n'):
                line = line.removesuffix('\r')
                match = PATCH_FILE_PATTERN.fullmatch(line)
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
        path = tool_args.get('path')
        if not isinstance(path, str) or not path:
            raise ValueError('path must be a non-empty string.')
        destinations.append(path)
    if any(not path or '\0' in path for path in [*destinations, *removed]):
        raise ValueError('file paths must be non-empty and must not contain NUL.')
    return destinations, removed


def absolute_path(cwd, path):
    # Keep trailing dots/spaces intact instead of letting Windows full-path APIs trim them.
    joined = os.path.join(os.path.abspath(cwd), path)
    if not os.path.isabs(joined):
        raise ValueError('file paths must resolve to absolute paths without drive-relative syntax.')
    return os.path.normpath(joined).replace(os.sep, '/')


def check_paths(cwd, destinations, removed):
    removed_paths = {absolute_path(cwd, path) for path in removed}
    planned = {}
    directory_entries = {}
    findings = []
    for destination in destinations:
        path = absolute_path(cwd, destination)
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
            key = path_key(child)
            # Existing names may be edited without introducing a new portability problem.
            if name not in entries:
                reason = invalid_name_reason(name)
                if reason:
                    findings.append(f'{destination!r}: {reason} ({name!r})')
                    break
                collision = next((entry for entry in entries if path_key(entry) == path_key(name)), None)
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
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')
    try:
        raw_input = sys.stdin.buffer.read().decode('utf-8')
        if not raw_input.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(raw_input)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip() or not os.path.isabs(cwd):
            raise ValueError('cwd must be a non-empty absolute path.')
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')
        tool_name = tool_name.removeprefix('functions.')
        if tool_name not in EDIT_TOOL_NAMES:
            return 0
        helpers = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'known-files-state.py',
        ))
        tool_args = helpers['get_tool_args'](hook_input['toolArgs'])
        if tool_name == 'apply_patch' and not isinstance(tool_args, (str, dict)):
            raise ValueError('apply_patch toolArgs must be a patch string or JSON object.')
        destinations, removed = get_operations(tool_name, tool_args, helpers)
        findings = check_paths(cwd, destinations, removed)
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
