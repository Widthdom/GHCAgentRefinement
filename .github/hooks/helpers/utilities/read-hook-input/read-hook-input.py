import json
import os
import sys

# hookの標準入力を読み、共通の項目を検証する。すべてのhookが共有する。
# 不正な入力はValueError（復号の失敗はその派生のUnicodeError）で知らせ、呼び出し元のhookが異常終了する。


def read_hook_input():
    # 標準入力をバイト列として読んでUTF-8で復号し、JSONオブジェクトとして返す。
    hook_input_json = sys.stdin.buffer.read().decode('utf-8')
    if not hook_input_json.strip():
        raise ValueError('hook input is empty.')
    hook_input = json.loads(hook_input_json)
    if not isinstance(hook_input, dict):
        raise ValueError('hook input must be a JSON object.')
    return hook_input


def get_cwd(hook_input):
    cwd = hook_input.get('cwd')
    if not isinstance(cwd, str) or not cwd.strip():
        raise ValueError('cwd must be a non-empty string.')
    if not os.path.isabs(cwd):
        raise ValueError('cwd must be an absolute path.')
    return cwd


def get_tool_call(hook_input):
    # preToolUse・postToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
    tool_name = hook_input.get('toolName')
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError('toolName must be a non-empty string.')
    if 'toolArgs' not in hook_input:
        raise ValueError('toolArgs is required.')
    return tool_name, hook_input['toolArgs']


def get_transcript_path(hook_input):
    transcript_path = hook_input.get('transcriptPath')
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        raise ValueError('transcriptPath must be a non-empty string.')
    if not os.path.isfile(transcript_path):
        raise ValueError(f'transcript path is not a file or could not be verified as a file: {transcript_path}')
    return transcript_path
