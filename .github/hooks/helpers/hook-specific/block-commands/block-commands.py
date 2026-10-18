import argparse
import codecs
import json
import os
import re
import sys
import unicodedata


def is_null_or_white_space(value):
    # .NETの[string]::IsNullOrWhiteSpace相当。文字列以外はPowerShellと同じく文字列化してから判定する。
    # Pythonのstr.isspace()は\x1c-\x1fも空白とみなすため使わない。
    text = to_ps_string(value)
    return all(
        char in '\t\n\v\f\r\x85\xa0' or unicodedata.category(char) in ('Zs', 'Zl', 'Zp')
        for char in text
    )


def to_ps_string(value):
    # PowerShellの[string]キャストや文字列展開と同じ規則で文字列化する。
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value).replace('e', 'E')
    if isinstance(value, list):
        return ' '.join(to_ps_string(item) for item in value)
    if isinstance(value, dict):
        if not value:
            return ''
        return '@{' + '; '.join(f'{key}={to_ps_string(item)}' for key, item in value.items()) + '}'
    return str(value)


def get_property(value, name):
    # PowerShellのプロパティアクセスは大文字小文字を区別しないため、それに合わせる。
    # 配列に対するアクセスはPowerShellのメンバー列挙と同様に各要素の値を集める。
    if isinstance(value, dict):
        if name in value:
            return value[name]
        lowered_name = name.lower()
        for key, item in value.items():
            if key.lower() == lowered_name:
                return item
        return None
    #   JSONオブジェクトの要素は、プロパティがなければNoneとして集め、それ以外の要素は読み飛ばす。
    if isinstance(value, list):
        items = []
        for element in value:
            if not isinstance(element, (dict, list)):
                continue
            item = get_property(element, name)
            if isinstance(element, list) and item is None:
                continue
            if isinstance(item, list):
                items.extend(item)
            else:
                items.append(item)
        if not items:
            return None
        return items[0] if len(items) == 1 else items
    return None


def has_property(value, name):
    return isinstance(value, dict) and any(key.lower() == name.lower() for key in value)


def build_json_object(pairs):
    # ConvertFrom-Jsonと同様に、同名キーは後勝ちとし、大文字小文字だけが異なるキーはエラーにする。
    result = {}
    for key, value in pairs:
        for existing_key in result:
            if existing_key != key and existing_key.lower() == key.lower():
                raise ValueError(
                    f"Cannot convert the JSON string because it contains keys with different casing. "
                    f"The key that was attempted to be added to the existing key '{existing_key}' was '{key}'."
                )
        result[key] = value
    return result


def parse_json(text):
    return json.loads(text, object_pairs_hook=build_json_object, strict=False)


def unroll(value):
    # -NoEnumerateなしのConvertFrom-Jsonは最外側の配列を展開する。
    # 要素数0は$null、1はその要素、2以上は配列として扱われる。
    if isinstance(value, list):
        if not value:
            return None
        return value[0] if len(value) == 1 else value
    return value


def read_all_text(path):
    # .NETのStreamReaderと同様にBOMから文字コードを判定し、BOMは内容に含めない。
    data = open(path, 'rb').read()
    for bom, encoding in (
        (codecs.BOM_UTF32_LE, 'utf-32-le'),
        (codecs.BOM_UTF32_BE, 'utf-32-be'),
        (codecs.BOM_UTF8, 'utf-8'),
        (codecs.BOM_UTF16_LE, 'utf-16-le'),
        (codecs.BOM_UTF16_BE, 'utf-16-be'),
    ):
        if data.startswith(bom):
            return data[len(bom):].decode(encoding, errors='replace')
    return data.decode('utf-8', errors='replace')


def to_compressed_json(value):
    # ConvertTo-Json -Compressと同様に、非ASCII文字はエスケープせず1行のJSONにする。
    # ConvertTo-Jsonがエスケープする\u0085、\u2028、\u2029も同様にエスケープする。
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return text.replace('\u0085', '\\u0085').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def write_error(message):
    print(message, file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser()
    # ブロック対象定義ファイルは引数として渡すことが可能
    # デフォルトは同ディレクトリの 'blocked-command-patterns.json'
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'blocked-command-patterns.json'),
    )
    arguments = parser.parse_args(argv)
    definitions_path = arguments.definitions_path

    # ブロック対象定義ファイルが無ければ異常終了。
    if not os.path.isfile(definitions_path):
        write_error(f'Blocked command definitions not found: {definitions_path}')
        return 2

    # ブロック対象定義ファイルを読み込む。
    #   空ファイルや空白のみのファイルはConvertFrom-Jsonと同様に定義なしとして扱う。
    try:
        definitions_text = read_all_text(definitions_path)
        definitions = None if is_null_or_white_space(definitions_text) else unroll(parse_json(definitions_text))
    except Exception as exception:
        write_error(f'Failed to parse blocked command definitions: {exception}')
        return 2

    # PowerShellのforeachと同様に、$nullは0件、配列以外は1件として扱う。
    if definitions is None:
        definitions = []
    elif not isinstance(definitions, list):
        definitions = [definitions]

    # 各定義を検証する。
    for definition in definitions:
        # 必須項目に欠落があれば異常終了。
        if (is_null_or_white_space(get_property(definition, 'name')) or
                is_null_or_white_space(get_property(definition, 'pattern')) or
                is_null_or_white_space(get_property(definition, 'reason'))):
            write_error('Each blocked command definition requires name, pattern, and reason.')
            return 2
        # 正規表現が正しく書けているか検証する。
        try:
            re.compile(to_ps_string(get_property(definition, 'pattern')))
        except Exception as exception:
            write_error(
                f"Invalid regular expression in blocked command definition "
                f"'{to_ps_string(get_property(definition, 'name'))}': {exception}"
            )
            return 2

    # 標準出力・標準エラーをUTF-8に統一することで、JSON内の日本語などを正しく出力する。BOMは出力されない。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    # preToolUseのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    #   改行コードを変換せずに扱うため、バイト列として読み取ってからUTF-8として復号化する。
    input_json = sys.stdin.buffer.read().decode('utf-8', errors='replace')
    if is_null_or_white_space(input_json):
        write_error('preToolUse hook input is empty.')
        return 2

    # 読み取ったJSONをPythonのオブジェクトに変換する。
    #   最外側の配列は展開しない。[{"toolName":"bash", ...}]のように最外側が[]のものは拒否したい。
    try:
        hook_input = parse_json(input_json)
    except Exception as exception:
        write_error(f'Failed to parse preToolUse hook input: {exception}')
        return 2

    # 変換結果がJSONオブジェクトでなければ異常終了。
    #   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
    if not isinstance(hook_input, dict):
        write_error('preToolUse hook input must be a JSON object.')
        return 2

    # `toolCalls` が存在する場合はその配列を、存在しない場合は単体入力を配列に変換したものを `tool_calls` に格納する。
    if has_property(hook_input, 'toolCalls'):
        tool_calls = get_property(hook_input, 'toolCalls')
        if not isinstance(tool_calls, list) or len(tool_calls) == 0:
            write_error('preToolUse hook toolCalls must be a non-empty array.')
            return 2
    else:
        tool_calls = [
            {
                'name': get_property(hook_input, 'toolName'),
                'args': get_property(hook_input, 'toolArgs'),
            }
        ]

    commands = []
    # 複数のツール呼び出しをまとめて要求される場合があるため、ひとつずつ処理する。
    for tool_call in tool_calls:
        # 各tool_callがJSONオブジェクトでなければ異常終了。
        if not isinstance(tool_call, dict):
            write_error('preToolUse hook requires each tool call to be a JSON object.')
            return 2

        # 各tool_callのnameが文字列でなければ異常終了。空白のみの文字列も許容しない。
        tool_name = get_property(tool_call, 'name')
        if not isinstance(tool_name, str) or is_null_or_white_space(tool_name):
            write_error('preToolUse hook requires a tool name for each tool call.')
            return 2

        # 以下に一致しない場合はスキップする。PowerShellの-notmatchと同様に大文字小文字を区別しない。
        #   bash
        #   powershell
        #   functions.bash
        #   functions.powershell
        if not re.search(r'^(?:functions\.)?(?:bash|powershell)$', tool_name, re.IGNORECASE):
            continue

        tool_args = get_property(tool_call, 'args')
        if isinstance(tool_args, str):
            command = tool_args
        elif isinstance(tool_args, dict) and isinstance(get_property(tool_args, 'command'), str):
            command = get_property(tool_args, 'command')
        else:
            command = None

        if is_null_or_white_space(command):
            write_error(f"preToolUse hook input for tool '{tool_name}' requires a non-empty command string.")
            return 2

        commands.append(command)

    # コマンドが各定義のパターンに一致するかを順に確認する。
    for command in commands:
        for definition in definitions:
            try:
                is_match = re.search(to_ps_string(get_property(definition, 'pattern')), command) is not None
            except Exception as exception:
                # 極めて稀なケースではあるが、正規表現の評価中に例外が発生する可能性があるため、ここで捕捉する。
                write_error(
                    f"Failed to match command against regular expression in blocked command definition "
                    f"'{to_ps_string(get_property(definition, 'name'))}': {exception}"
                )
                return 2

            if is_match:
                # 拒否とその理由をJSON形式に変換して返却する。
                print(to_compressed_json({
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': get_property(definition, 'reason'),
                }))
                return 0

    # 標準出力へ空のJSONオブジェクトを返すことで、「拒否する判断はない」ことを呼び出し元に伝える。
    print('{}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
