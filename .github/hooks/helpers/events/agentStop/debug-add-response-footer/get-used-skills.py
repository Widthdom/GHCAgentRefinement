import os
import runpy


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_get_turns = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))['get_turns']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


def get_used_skills(transcript_path):
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f'Transcript file not found: {transcript_path}')

    # ユーザーの最新の依頼以降のイベントを対象とする。hookの差し戻し（フッター修正の依頼を含む）は依頼の区切りとしない。
    used_skills = []
    for event_type, data in _get_turns(transcript_path)[-1]:
        if event_type != 'tool.execution_start' or data.get('toolName') not in ('skill', 'functions.skill'):
            continue

        # 引数はJSON文字列で記録される場合もある。オブジェクトでない引数はskill名がないものとして読み飛ばす。
        arguments = _unwrap_tool_args(data.get('arguments'))
        skill_name = arguments.get('skill') if isinstance(arguments, dict) else None
        if isinstance(skill_name, str) and skill_name.strip() and skill_name not in used_skills:
            used_skills.append(skill_name)

    return used_skills
