import os
import stat
import sys

from common import (
    equals_ignore_case,
    get_property,
    is_null_or_white_space,
    parse_json_enumerated,
    read_all_text,
    read_lines,
    test_response_footer_correction_message,
    to_ps_string,
)


def _is_hidden(entry):
    # Get-ChildItemは-Forceなしでは隠しファイルを列挙しないため、それに合わせる。
    if os.name == 'nt':
        return bool(entry.stat().st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)
    return entry.name.startswith('.')


def get_used_hooks(transcript_path, hooks_path):
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f'Transcript file not found: {transcript_path}')

    if not os.path.isdir(hooks_path):
        raise FileNotFoundError(f'Hooks directory not found: {hooks_path}')

    # 大文字小文字を区別しない集合として、小文字化した値を保持する。
    hook_types = set()

    for line in read_lines(transcript_path):
        if is_null_or_white_space(line):
            continue

        event = parse_json_enumerated(line)

        if (equals_ignore_case(get_property(event, 'type'), 'user.message') and
                not test_response_footer_correction_message(get_property(get_property(event, 'data'), 'content'))):
            hook_types.clear()
            continue

        hook_type = get_property(get_property(event, 'data'), 'hookType')
        if (equals_ignore_case(get_property(event, 'type'), 'hook.start') and
                not is_null_or_white_space(hook_type)):
            hook_types.add(to_ps_string(hook_type).lower())

    hook_files = [
        entry for entry in os.scandir(hooks_path)
        if entry.is_file() and entry.name.lower().endswith('.json') and not _is_hidden(entry)
    ]
    used_hooks = []
    for hook_file in sorted(hook_files, key=lambda entry: entry.name.lower()):
        hook_definition = parse_json_enumerated(read_all_text(hook_file.path))
        configured_hooks = get_property(hook_definition, 'hooks')
        configured_hook_types = list(configured_hooks.keys()) if isinstance(configured_hooks, dict) else []

        if any(hook_type.lower() in hook_types for hook_type in configured_hook_types):
            used_hooks.append(os.path.splitext(hook_file.name)[0])

    return used_hooks


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--transcript-path', required=True)
    parser.add_argument('--hooks-path', required=True)
    arguments = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    for used_hook in get_used_hooks(arguments.transcript_path, arguments.hooks_path):
        print(used_hook)
