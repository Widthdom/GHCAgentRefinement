import io
import os
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


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='backslashreplace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        # サブエージェントへの依頼のときだけ、入力にagentIdが含まれる。
        # 親のセッションにはsessionStartのload-copilot-instructions hookが指示を渡すため、ここでは何もしない。
        agent_id = hook_input.get('agentId')
        if agent_id is None:
            return 0
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError('agentId must be a non-empty string.')
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to parse userPromptSubmitted hook input: {ex}', file=sys.stderr)
        return 2

    # サブエージェントではsessionStartが発火しないため、親と同じ指示ファイルを依頼文に添えて渡す。
    # utilities → helpers → hooks → .github の順に上がる。
    instructions_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(_utilities_directory))), 'copilot-instructions.md',
    )
    if not os.path.isfile(instructions_path):
        print(f'Copilot instructions file not found: {instructions_path}', file=sys.stderr)
        return 2

    # `utf-8-sig` を指定することで、BOMが存在する場合は取り除いて読むことができる。
    try:
        with open(instructions_path, encoding='utf-8-sig', newline='') as instructions_file:
            instructions = instructions_file.read()
    except (OSError, UnicodeError) as ex:
        print(f'Failed to read Copilot instructions file: {instructions_path}: {ex}', file=sys.stderr)
        return 2

    if not instructions.strip():
        print(f'Copilot instructions file is empty: {instructions_path}', file=sys.stderr)
        return 2

    # SDK形式のトップレベルadditionalContext。依頼文に添えてサブエージェントへ渡される。
    _hook_output['write_additional_context'](instructions)
    return 0


if __name__ == '__main__':
    sys.exit(main())
