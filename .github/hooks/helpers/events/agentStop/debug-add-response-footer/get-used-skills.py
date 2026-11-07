import os
import runpy


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)


def get_used_skills(transcript_path):
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f'Transcript file not found: {transcript_path}')

    used_skills = []

    for event in runpy.run_path(
        os.path.join(_utilities_directory, 'read-jsonl-objects', 'read-jsonl-objects.py')
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
