import io
import os
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'debug_mode_state'),
]
import read_hook_input as _hook_input
import debug_mode_state as _debug_mode_state


def main():
    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # userPromptSubmittedのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにセッションIDやプロンプトを含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    # 状態ファイルの保存先と形式は、状態を読むhookとdebug_mode_state utilityで共有する。
    try:
        hook_input = _hook_input.read_hook_input()
        # getはキーが無くてもNoneを返す。
        prompt = hook_input.get('prompt')
        if not isinstance(prompt, str):
            raise ValueError('prompt must be a string.')
        if not prompt.strip():
            raise ValueError('prompt must not be empty or whitespace.')
        state_path = _debug_mode_state.get_debug_mode_path(hook_input.get('sessionId'))
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to parse userPromptSubmitted hook input: {ex}', file=sys.stderr)
        return 2

    # agentIdが存在する場合は、サブエージェントのコンテキストで動作していることを示すので、
    # 状態の上書きを避けるために終了する。
    #   サブエージェントへの依頼でもsessionIdは親と同じため、上書きすると親のデバッグモードが途中で解除される。
    if hook_input.get('agentId'):
        return 0

    # プロンプト先頭が厳密に ?? であれば有効にする。
    enabled = prompt.startswith('??')

    try:
        _debug_mode_state.save_debug_mode(state_path, enabled)
    except OSError as ex:
        print(f'Failed to save debug mode: {ex}', file=sys.stderr)
        return 2

    return 0


if __name__ == '__main__':
    sys.exit(main())
