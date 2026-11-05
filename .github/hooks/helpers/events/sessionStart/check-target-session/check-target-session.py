import io
import json
import sys
from typing import cast


def main():
    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # sessionStartのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
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
            print('sessionStart hook input is empty.', file=sys.stderr)
            return 2
        hook_input = json.loads(hook_input_json)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f'Failed to parse sessionStart hook input: {ex}', file=sys.stderr)
        return 2

    # 変換結果がJSONオブジェクト（最外側が{}）でなければ異常終了。
    #   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
    if not isinstance(hook_input, dict):
        print('sessionStart hook input must be a JSON object.', file=sys.stderr)
        return 2

    hook_event_name = hook_input.get('hook_event_name')

    # Copilot CLI / SDK ではこのキーがない想定なので、None の場合は何も出力せず正常終了する。
    if hook_event_name is None:
        return 0

    # VS Code Local harness の payload のイベント名は先頭が大文字の 'SessionStart' で、hook 設定の 'sessionStart' とは表記が異なる。
    # Python の文字列比較は大文字小文字を区別するため、この値に完全一致する場合だけ案内を出力する。
    if hook_event_name != 'SessionStart':
        return 0

    # JSONを構築して標準出力する。
    # separators=(',', ':')：項目間やキーと値の間に余分な空白を入れない。（出力をコンパクトにするための指定で、省略してもJSONとしては正しく伝わる。）
    # 引用符・バックスラッシュ・改行など、JSONとして必要なエスケープは ensure_ascii=False で行われる
    print(json.dumps({
        'systemMessage': (
            "This session is using the VS Code Local harness. Open the Agents window, create a new session, "
            "and select Copilot as the Session Target to use this repository's Copilot CLI-compatible hooks. "
            "Continue this task in that new session."
        ),
    }, ensure_ascii=False, separators=(',', ':')))
    return 0

# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
