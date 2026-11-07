import io
import json
import os
import sys
from typing import cast


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', newline='\n')
    # backslashreplace：変換できない文字があったら、例外を出さずに \uXXXX の形式で置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   replace：? に置き換える。
    #   ignore：その文字を捨てる。
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='backslashreplace', newline='\n')

    # __file__：このスクリプト自身の絶対パスが設定されている組み込み変数。
    # os.path.abspath：絶対パス化（シンボリックリンクは解決しない）
    # hookのフォルダー → イベントのフォルダー → events → helpers → hooks → .github の順に上がる。
    github_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)),
    )))))
    instructions_path = os.path.join(github_directory, 'copilot-instructions.md')
    if not os.path.isfile(instructions_path):
        print(f'Copilot instructions file not found: {instructions_path}', file=sys.stderr)
        return 2

    # `utf-8-sig` を指定することで、BOMが存在する場合は取り除いて読むことができる。
    # with：処理を抜けるときの後片付けを自動で行う構文（C#のusingに相当）。
    with open(instructions_path, encoding='utf-8-sig', newline='') as instructions_file:
        instructions = instructions_file.read()

    # Pythonの not は、対象を真偽として評価してから反転する。
    # strip() が返すのは文字列だが、真偽として判定されれば空文字列はFalse、それ以外はTrueとなる。
    #   if not text:   # 文字列が空のとき
    #   if not items:  # リストなどが空のとき
    #   if not data:   # 辞書などが空のとき
    if not instructions.strip():
        print(f'Copilot instructions file is empty: {instructions_path}', file=sys.stderr)
        return 2

    # ensure_ascii=False：日本語などを \uXXXX にせず、そのまま出力する。出力先はUTF-8に設定するため問題なし。
    # separators=(',', ':')：項目間やキーと値の間に余分な空白を入れない。（出力をコンパクトにするための指定で、省略してもJSONとしては正しく伝わる。）
    # 引用符・バックスラッシュ・改行など、JSONとして必要なエスケープは ensure_ascii=False で行われる
    print(json.dumps({'additionalContext': instructions}, ensure_ascii=False, separators=(',', ':')))

    return 0


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
