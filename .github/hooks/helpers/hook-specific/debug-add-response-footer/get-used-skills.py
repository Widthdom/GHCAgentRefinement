import os
import sys

from common import (
    equals_ignore_case,
    get_property,
    is_null_or_white_space,
    not_equals_ignore_case,
    parse_json_enumerated,
    read_lines,
    test_response_footer_correction_message,
    to_ps_string,
)


def _is_skill_tool(tool_name):
    # PowerShellの-inと同様に、大文字小文字を区別せずに比較する。配列は一致しないものとして扱う。
    if isinstance(tool_name, list):
        return False
    return to_ps_string(tool_name).lower() in ('skill', 'functions.skill')


def get_used_skills(transcript_path):
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f'Transcript file not found: {transcript_path}')

    used_skills = []

    for line in read_lines(transcript_path):
        if is_null_or_white_space(line):
            continue

        event = parse_json_enumerated(line)

        if (equals_ignore_case(get_property(event, 'type'), 'user.message') and
                not test_response_footer_correction_message(get_property(get_property(event, 'data'), 'content'))):
            used_skills.clear()
            continue

        if (not_equals_ignore_case(get_property(event, 'type'), 'tool.execution_start') or
                not _is_skill_tool(get_property(get_property(event, 'data'), 'toolName'))):
            continue

        skill_name = get_property(get_property(get_property(event, 'data'), 'arguments'), 'skill')
        if not is_null_or_white_space(skill_name) and to_ps_string(skill_name) not in used_skills:
            used_skills.append(to_ps_string(skill_name))

    return used_skills


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--transcript-path', required=True)
    arguments = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    for used_skill in get_used_skills(arguments.transcript_path):
        print(used_skill)
