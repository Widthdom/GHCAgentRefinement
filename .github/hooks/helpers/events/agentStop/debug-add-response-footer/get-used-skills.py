import io
import os
import runpy
import sys
from pathlib import Path
from typing import cast


def get_used_skills(transcript_path):
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f'Transcript file not found: {transcript_path}')

    used_skills = []

    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        content = data.get('content')
        if (event.get('type') == 'user.message' and
                not (isinstance(content, str) and
                     content.startswith('The final response footer is missing or incorrect.'))):
            used_skills.clear()
            continue

        if (event.get('type') != 'tool.execution_start' or
                data.get('toolName') not in ('skill', 'functions.skill')):
            continue

        arguments = data.get('arguments', {})
        if not isinstance(arguments, dict):
            raise ValueError('Skill tool arguments must be a JSON object.')
        skill_name = arguments.get('skill')
        if isinstance(skill_name, str) and skill_name.strip() and skill_name not in used_skills:
            used_skills.append(skill_name)

    return used_skills


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--transcript-path', required=True)
    arguments = parser.parse_args()
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8')
    for used_skill in get_used_skills(arguments.transcript_path):
        print(used_skill)
