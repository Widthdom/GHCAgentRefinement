import io
import json
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


def main():
    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # sessionStartのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにセッションIDや起動の種類を含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    #   空、JSONとして解析できない、JSONオブジェクト（最外側が{}）でない入力は異常終了。
    try:
        hook_input = _hook_input['read_hook_input']()
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to parse sessionStart hook input: {ex}', file=sys.stderr)
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
    # 引用符・バックスラッシュ・改行など、JSONとして必要なエスケープは、ensure_asciiの指定によらずjson.dumpsが常に行う。
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
