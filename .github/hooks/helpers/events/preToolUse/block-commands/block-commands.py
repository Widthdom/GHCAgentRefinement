import argparse
import io
import json
import os
import re
import sys
from typing import cast


def is_non_empty_string(value):
    # `isinstance` は Pythonの組み込み関数であり、C#の `is` に相当する。
    # `strip` は文字列型strのメソッドであり、文字列の両端から空白文字を削除する。空白のみの文字列は空文字列に変換される。C#の `Trim` に相当する。
    # bool は Pythonの組み込み関数である。
    #   bool("")       # False（引数が文字列のときは空文字列以外がTRUE）
    #   bool("hello")  # True
    #   bool(0)        # False（引数が数値のときは0以外がTRUE）
    #   bool(123)      # True
    #   bool([])       # False（引数がリストのときは空リスト以外がTRUE）
    #   bool([1, 2])   # True
    #   bool(None)     # False（引数がNoneのときはFALSE）
    return isinstance(value, str) and bool(value.strip())


# 引数を拒否理由とするJSONを構築して標準出力する。
def write_stdout_deny(reason):
    # ensure_ascii=False：日本語などを \uXXXX にせず、そのまま出力する。出力先はUTF-8に設定するため問題なし。
    # separators=(',', ':')：項目間やキーと値の間に余分な空白を入れない。（出力をコンパクトにするための指定で、省略してもJSONとしては正しく伝わる。）
    # 引用符・バックスラッシュ・改行など、JSONとして必要なエスケープは ensure_ascii=False で行われる
    print(json.dumps({
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason,
    }, ensure_ascii=False, separators=(',', ':')))


# 引数を標準エラー出力するとともに、write_stdout_denyを呼び出す。
def write_stderr_msg_and_deny(msg):
    print(msg, file=sys.stderr)
    write_stdout_deny(msg)


def main(definitions_path=None):
    # 通常実行では指定不要。テスト用に解析対象の引数リストを差し替えられるようにする（現行テストでは未使用）。
    # definitions_pathはパス単体ではなく、['--definitions-path', 'rules.json']のようなリストを受け取る。

    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # モジュールargparseのArgumentParserクラスのコンストラクタ呼び出し
    parser = argparse.ArgumentParser()
    # テスト用にブロック対象定義ファイルを（--definitions-path オプションで指定して）差し替えられるようにする。
    # __file__：このスクリプト自身の絶対パスが設定されている組み込み変数。
    # オプション名のハイフンはアンダースコアに変換されるので、以降オプションを参照するときはアンダースコアを使う。
    # add_argument('--definitions-path', dest='path')とすれば、以降オプションを参照するときは.pathでアクセスできるようになる。
    #   dest指定文字列はハイフンからアンダースコアに自動変換されない。
    parser.add_argument(
        '--definitions-path',
        dest='defs_path',
        default=os.path.join(os.path.dirname(__file__), 'blocked-command-definitions.json'),
    )

    # コマンドライン引数の解析と取得
    # parse_args：引数がNoneのときはsys.argv[1:]（実際のコマンドライン引数からスクリプト名を除いたもの）を解析する。
    parsed_args = parser.parse_args(definitions_path)

    # ブロック対象定義ファイルが見つからない、または存在を確認できない場合は異常終了。
    # f'...' は formatted string literalのことで、文字列の中に {式} の値を埋め込める書き方。C#の $"..." に相当する。
    if not os.path.exists(parsed_args.defs_path):
        write_stderr_msg_and_deny(f'Blocked command definitions not found or existence could not be verified: {parsed_args.defs_path}')
        return 2

    # ブロック対象定義ファイルパスがファイルではない、またはファイルであることを確認できない場合は異常終了。
    if not os.path.isfile(parsed_args.defs_path):
        write_stderr_msg_and_deny(f'Blocked command definitions path is not a file or could not be verified as a file: {parsed_args.defs_path}')
        return 2

    # 定義ファイルはUTF-8のJSON配列として読み込む。UTF-8 BOMは許容する。
    # sig は signatureの略でありここではByte Order Mark (BOM) を指す。
    # BOMはテキストファイルのバイト列先頭に付けることがある、文字コードなどを識別するための目印。（UTF-8ではBOMなしも一般的）
    # `utf-8-sig` を指定することで、BOMが存在する場合は取り除いて読むことができる。
    try:
        # with：処理を抜けるときの後片付けを自動で行う構文（C#のusingに相当）。
        with open(parsed_args.defs_path, encoding='utf-8-sig') as defs_json_array:
            definitions = json.load(defs_json_array)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        write_stderr_msg_and_deny(f'Failed to parse blocked command definitions: {ex}')
        return 2

    # blocked-command-definitions.jsonはJSON配列すなわち最外側が[]なので、前段のjson.loadによりdefinitionsはlistとなる。
    if not isinstance(definitions, list):
        write_stderr_msg_and_deny('Blocked command definitions must be a JSON array.')
        return 2

    # 各定義を検証する。
    compiled_definitions = []
    for definition in definitions:
        # blocked-command-definitions.jsonのJSON配列要素は{}なので、前段のjson.loadによりdefinitionはdictとなる。
        if not isinstance(definition, dict):
            write_stderr_msg_and_deny('Each blocked command definition must be a JSON object.')
            return 2

        # 必須項目に欠落があれば異常終了。
        # 各キーの値を取得して、非空文字列か否かの結果を確認し、ひとつでもFalseであれば異常終了。
        # allは、前から評価してFalseが見つかった時点で即座にFalseを返す。
        #   ここではis_non_empty_string(definition.get('pattern'))がfalseならis_non_empty_string(definition.get('reason'))は実行されない。
        if not all(
            # getはキーが無くてもNoneを返す。ここでdefinitions[key]のようにするとKeyErrorが発生する可能性がある。
            is_non_empty_string(definition.get(key)) for key in ('name', 'pattern', 'reason')
        ):
            write_stderr_msg_and_deny('Each blocked command definition requires non-empty strings for name, pattern, and reason.')
            return 2
            
        # 正規表現が正しく書けているか検証する。
        try:
            # re：正規表現（regular expression）モジュール。
            compiled_pattern = re.compile(definition['pattern'])
        except re.PatternError as ex:
            write_stderr_msg_and_deny(f"Invalid regular expression in blocked command definition '{definition['name']}': {ex}")
            return 2

        # コンパイル済みの正規表現と理由をturpleとしてListに追加する。
        compiled_definitions.append((compiled_pattern, definition['reason']))

    # preToolUseのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
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
            write_stderr_msg_and_deny('preToolUse hook input is empty.')
            return 2
        hook_input = json.loads(hook_input_json)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        write_stderr_msg_and_deny(f'Failed to parse preToolUse hook input: {ex}')
        return 2

    # 変換結果がJSONオブジェクト（最外側が{}）でなければ異常終了。
    #   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
    if not isinstance(hook_input, dict):
        write_stderr_msg_and_deny('preToolUse hook input must be a JSON object.')
        return 2

    # dictであるhook_inputに、`toolCalls` キーが存在するかどうかを判定する。
    if 'toolCalls' in hook_input:
        tool_calls = hook_input['toolCalls']
        # 配列（最外側が[]）でなければ異常終了。
        if not isinstance(tool_calls, list):
            write_stderr_msg_and_deny('preToolUse hook toolCalls must be an array.')
            return 2

        # 空配列なら異常終了。
        if not tool_calls:
            write_stderr_msg_and_deny('preToolUse hook toolCalls must be a non-empty array.')
            return 2
    else:
        # このelseブロックは、以下のように（"toolCalls"配列になっていない）単体のツール呼び出しへの備えである。
        #   {
        #       "toolName": "...",
        #       "toolArgs": { "command": "..." }
        #   }
        # 一方でifブロックは、以下のように（"toolCalls" 配列として）複数のツールの一括呼び出しへ対応している。
        #   {
        #       "toolCalls": [
        #           {"name": "bash", "args": {"command": "git status"}},
        #           {"name": "powershell", "args": {"command": "git reset"}}
        #       ]
        #   }
        tool_calls = [
            {
                'name': hook_input.get('toolName'),
                'args': hook_input.get('toolArgs'),
            }
        ]

    commands = []
    # 複数のツール呼び出しをまとめて要求される場合があるため、ひとつずつ処理する。
    for tool_call in tool_calls:
        # 各tool_callがJSONオブジェクトでなければ異常終了。
        if not isinstance(tool_call, dict):
            write_stderr_msg_and_deny('preToolUse hook requires each tool call to be a JSON object.')
            return 2

        # 各tool_callのnameが文字列でなければ異常終了。空白のみの文字列も許容しない。
        tool_name = tool_call.get('name')
        if not is_non_empty_string(tool_name):
            write_stderr_msg_and_deny('preToolUse hook requires a tool name for each tool call.')
            return 2

        # 以下に一致しない場合はスキップする。大文字小文字は区別しない。
        #   bash
        #   powershell
        #   functions.bash
        #   functions.powershell
        if not re.search(r'^(?:functions\.)?(?:bash|powershell)$', tool_name, re.IGNORECASE):
            continue

        tool_args = tool_call.get('args')
        if isinstance(tool_args, str):
            command = tool_args
        elif isinstance(tool_args, dict):
            command = tool_args.get('command')
        else:
            command = None

        if not is_non_empty_string(command):
            write_stderr_msg_and_deny(f"preToolUse hook input for tool '{tool_name}' requires a non-empty command string.")
            return 2

        commands.append(command)

    # コマンドが各定義のパターンに一致するかを順に確認する。
    for command in commands:
        for compiled_pattern, reason in compiled_definitions:
            if compiled_pattern.search(command):
                # 拒否とその理由をJSON形式に変換して返却する。
                write_stdout_deny(reason)
                return 0

    # 標準出力へ空のJSONオブジェクトを返すことで、「拒否する判断はない」ことを呼び出し元に伝える。
    print('{}')
    return 0


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
