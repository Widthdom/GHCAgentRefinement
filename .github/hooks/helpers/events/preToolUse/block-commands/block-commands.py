import argparse
import io
import os
import re
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
_definitions = runpy.run_path(os.path.join(_utilities_directory, 'read-definitions', 'read-definitions.py'))
_tool_calls = runpy.run_path(os.path.join(_utilities_directory, 'parse-tool-calls', 'parse-tool-calls.py'))


def _is_non_blank_string(value):
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


# 引数を標準エラー出力するとともに、拒否理由として標準出力する。
def _write_stderr_msg_and_deny(msg):
    print(msg, file=sys.stderr)
    _hook_output['write_deny_decision'](msg)


def _load_definitions(path):
    # 定義ファイルは、name・pattern・reasonを持つオブジェクトの、空でないJSON配列として読み込む。UTF-8 BOMは許容する。
    # 戻り値は (コンパイル済みの正規表現, 拒否理由) のリスト。
    # 定義ファイルがなければOSError、形式や正規表現が不正ならValueErrorを送出する。
    label = 'blocked command definitions'
    definitions = _definitions['read_definitions'](path, label, list)
    named_patterns = _definitions['build_named_regex_matchers'](definitions, label)
    # name・patternの検証で、各定義がJSONオブジェクトであることは確かめ済み。
    if not all(_is_non_blank_string(definition.get('reason')) for definition in definitions):
        raise ValueError(f'each item of {label} requires a non-blank string for reason.')
    return [(pattern, definition['reason']) for (_, pattern), definition in zip(named_patterns, definitions)]


def main(arguments=None):
    # 通常実行では指定不要。テスト用に解析対象の引数リスト（['--definitions-path', 'rules.json'] など）を差し替えられるようにする。

    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

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
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'blocked-command-definitions.json'),
    )

    # コマンドライン引数の解析と取得
    # parse_args：引数がNoneのときはsys.argv[1:]（実際のコマンドライン引数からスクリプト名を除いたもの）を解析する。
    parsed_args = parser.parse_args(arguments)

    # ブロック対象定義ファイルを読み込み、各定義を検証する。
    # 定義を読めない、または不正な場合は、危険なコマンドを止められないため、異常終了するとともにツール実行を拒否する。
    try:
        compiled_definitions = _load_definitions(parsed_args.defs_path)
    except (OSError, ValueError) as ex:
        _write_stderr_msg_and_deny(f'Failed to load blocked command definitions: {ex}')
        return 2

    # preToolUseのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    #   空、JSONとして解析できない、JSONオブジェクト（最外側が{}）でない入力は異常終了。
    try:
        hook_input = _hook_input['read_hook_input']()
    except (OSError, UnicodeError, ValueError) as ex:
        _write_stderr_msg_and_deny(f'Failed to parse preToolUse hook input: {ex}')
        return 2

    # preToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
    #   {
    #       "toolName": "...",
    #       "toolArgs": { "command": "..." }
    #   }
    # toolNameが文字列でなければ異常終了。空白のみの文字列も許容しない。
    tool_name = hook_input.get('toolName')
    if not _is_non_blank_string(tool_name):
        _write_stderr_msg_and_deny('preToolUse hook requires a tool name.')
        return 2

    # 以下に一致しない場合は許可する。大文字小文字は区別しない。
    #   bash
    #   powershell
    #   functions.bash
    #   functions.powershell
    if not re.search(r'^(?:functions\.)?(?:bash|powershell)$', tool_name, re.IGNORECASE):
        print('{}')
        return 0

    # toolArgsはJSON文字列に包まれて渡される場合もあるため、包みを外してから取り出す。
    # JSONとして解析できない文字列だけを、生のコマンドとして扱う。
    tool_args = _tool_calls['unwrap_tool_args'](hook_input.get('toolArgs'))
    if isinstance(tool_args, str):
        command = tool_args
    elif isinstance(tool_args, dict):
        command = tool_args.get('command')
    else:
        command = None

    if not _is_non_blank_string(command):
        _write_stderr_msg_and_deny(f"preToolUse hook input for tool '{tool_name}' requires a non-empty command string.")
        return 2

    # コマンドが各定義のパターンに一致するかを順に確認する。
    for compiled_pattern, reason in compiled_definitions:
        if compiled_pattern.search(command):
            # 拒否とその理由をJSON形式に変換して返却する。
            _hook_output['write_deny_decision'](reason)
            return 0

    # 標準出力へ空のJSONオブジェクトを返すことで、「拒否する判断はない」ことを呼び出し元に伝える。
    print('{}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
