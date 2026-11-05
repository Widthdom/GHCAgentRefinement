import io
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from typing import cast


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
    #   改行コードを変換せずに扱うため、バイト列として読み取ってからUTF-8として復号化する。
    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        # Pythonの not は、対象を真偽として評価してから反転する。
        # strip() が返すのは文字列だが、真偽として判定されれば空文字列はFalse、それ以外はTrueとなる。
        #   if not text:   # 文字列が空のとき
        #   if not items:  # リストなどが空のとき
        #   if not data:   # 辞書などが空のとき
        if not hook_input_json.strip():
            print('userPromptSubmitted hook input is empty.', file=sys.stderr)
            return 2
        hook_input = json.loads(hook_input_json)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f'Failed to parse userPromptSubmitted hook input: {ex}', file=sys.stderr)
        return 2

    # 変換結果がJSONオブジェクト（最外側が{}）でなければ異常終了。
    #   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
    if not isinstance(hook_input, dict):
        print('userPromptSubmitted hook input must be a JSON object.', file=sys.stderr)
        return 2

    # getはキーが無くてもNoneを返す。
    prompt = hook_input.get('prompt')

    if not isinstance(prompt, str):
        print('userPromptSubmitted hook prompt must be a string.', file=sys.stderr)
        return 2

    if not prompt.strip():
        print('userPromptSubmitted hook prompt must not be empty or whitespace.', file=sys.stderr)
        return 2

    # getはキーが無くてもNoneを返す。
    session_id = hook_input.get('sessionId')

    if not isinstance(session_id, str):
        print('userPromptSubmitted hook sessionId must be a string.', file=sys.stderr)
        return 2

    if not session_id.strip():
        print('userPromptSubmitted hook sessionId must not be empty or whitespace.', file=sys.stderr)
        return 2

    try:
        parsed_session_id = uuid.UUID(session_id.strip())
    except ValueError:
        print('userPromptSubmitted hook sessionId must be a valid UUID.', file=sys.stderr)
        return 2

    # agentIdが存在する場合は、サブエージェントのコンテキストで動作していることを示すので、
    # 状態の上書きを避けるために終了する。
    #   サブエージェントへの依頼でもsessionIdは親と同じため、上書きすると親のデバッグモードが途中で解除される。
    if hook_input.get('agentId'):
        return 0

    # tempfile.gettempdir() は、環境変数 TMPDIR・TEMP・TMP や OS 標準の候補から、利用可能な一時フォルダーを選んで返す。
    state_directory = os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'debug-mode')

    # 状態ファイルのパスを決定。
    state_path = os.path.join(state_directory, f'{parsed_session_id}.json')

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
