import io
import json
import os
import runpy
import sys
from datetime import datetime, timezone
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']


def main():
    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # userPromptSubmittedのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    # 状態ファイルの保存先は、状態を読むget-debug-mode utilityと共有する。
    try:
        hook_input = _hook_input['read_hook_input']()
        # getはキーが無くてもNoneを返す。
        prompt = hook_input.get('prompt')
        if not isinstance(prompt, str):
            raise ValueError('prompt must be a string.')
        if not prompt.strip():
            raise ValueError('prompt must not be empty or whitespace.')
        state_path = _get_state_path('debug-mode', hook_input.get('sessionId'), '.json')
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to parse userPromptSubmitted hook input: {ex}', file=sys.stderr)
        return 2
    state_directory = os.path.dirname(state_path)

    # agentIdが存在する場合は、サブエージェントのコンテキストで動作していることを示すので、
    # 状態の上書きを避けるために終了する。
    #   サブエージェントへの依頼でもsessionIdは親と同じため、上書きすると親のデバッグモードが途中で解除される。
    if hook_input.get('agentId'):
        return 0

    # プロンプト先頭が厳密に ?? であれば有効にする。
    enabled = prompt.startswith('??')

    # 状態ファイルに保存する内容をdictとして作成する。
    # 内部的な記録なのでUTCを使用する。
    # isoformat() で日時オブジェクトをISO 8601形式の文字列に変換する。
    state = {
        'updatedAt': datetime.now(timezone.utc).isoformat(),
        'enabled': enabled,
    }

    try:
        # exist_ok=True を指定して、すでにディレクトリが存在していてもエラーにしない。
        os.makedirs(state_directory, exist_ok=True)

        # with：処理を抜けるときの後片付けを自動で行う構文（C#のusingに相当）。
        # 'w'：書き込みモード。既存のファイルがあれば上書きされる。
        # newline='' を指定して、改行コードの自動変換を抑制する。
        with open(state_path, 'w', encoding='utf-8', newline='') as state_json:
            json.dump(state, state_json, ensure_ascii=False, separators=(',', ':'))
    except OSError as ex:
        print(f'Failed to save debug mode: {ex}', file=sys.stderr)
        return 2

    return 0


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
