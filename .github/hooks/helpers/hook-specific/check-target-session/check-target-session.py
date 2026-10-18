import json
import sys
import unicodedata


def is_null_or_white_space(value):
    # .NETの[string]::IsNullOrWhiteSpace相当。Pythonのstr.isspace()は\x1c-\x1fも空白とみなすため使わない。
    return all(
        char in '\t\n\v\f\r\x85\xa0' or unicodedata.category(char) in ('Zs', 'Zl', 'Zp')
        for char in value
    )


def get_property(value, name):
    # PowerShellのプロパティアクセスは大文字小文字を区別しないため、それに合わせる。
    # 配列に対するアクセスはPowerShellのメンバー列挙と同様に各要素の値を集める。
    #   JSONオブジェクトの要素は、プロパティがなければNoneとして集め、それ以外の要素は読み飛ばす。
    if isinstance(value, dict):
        if name in value:
            return value[name]
        lowered_name = name.lower()
        for key, item in value.items():
            if key.lower() == lowered_name:
                return item
        return None
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


def equals_case_sensitive(left, right):
    # PowerShellの-ceqと同様に、左辺の型に合わせて右辺の文字列を変換して比較する。
    if isinstance(left, str):
        return left == right
    if isinstance(left, bool):
        return left == (len(right) > 0)
    if isinstance(left, (int, float)):
        try:
            return left == float(right)
        except ValueError:
            return False
    return False


def is_truthy(value):
    # PowerShellの真偽値変換と同じ規則で判定する。
    if value is None:
        return False
    if isinstance(value, list):
        if not value:
            return False
        return is_truthy(value[0]) if len(value) == 1 else True
    if isinstance(value, (bool, int, float, str)):
        return bool(value)
    return True


def not_equals_case_sensitive(left, right):
    # PowerShellの-cneと同様に、左辺が配列の場合は条件に合う要素を抽出した結果で真偽を判定する。
    if isinstance(left, list):
        return is_truthy([item for item in left if not equals_case_sensitive(item, right)])
    return not equals_case_sensitive(left, right)


def to_compressed_json(value):
    # ConvertTo-Json -Compressと同様に、非ASCII文字はエスケープせず1行のJSONにする。
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def write_error(message):
    print(message, file=sys.stderr)


def main():
    # 標準出力・標準エラーをUTF-8に統一することで、JSON内の日本語などを正しく出力する。BOMは出力されない。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    # sessionStartのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    #   改行コードを変換せずに扱うため、バイト列として読み取ってからUTF-8として復号化する。
    input_json = sys.stdin.buffer.read().decode('utf-8', errors='replace')
    if is_null_or_white_space(input_json):
        write_error('sessionStart hook input is empty.')
        return 2

    # 読み取ったJSONをPythonのオブジェクトに変換する。
    #   最外側が[]のものは拒否したいので、配列は展開せずそのまま保持する。
    try:
        hook_input = parse_json(input_json)
    except Exception as exception:
        write_error(f'Failed to parse sessionStart hook input: {exception}')
        return 2

    # 変換結果がJSONオブジェクトでなければ異常終了。
    #   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
    if not isinstance(hook_input, dict):
        write_error('sessionStart hook input must be a JSON object.')
        return 2

    # VS Code Local harness はフック実行時の payload に hook_event_name: 'SessionStart' を含めて渡してくる。
    # 一方、Copilot CLI や Session Target 'Copilot'（Copilot SDK形式）ではこのプロパティが含まれない。
    # そのため、hook_event_name が大文字小文字を区別して 'SessionStart' に等しい場合を除き、
    # Copilot target 等と判断して何も出力せず終了する（exit 0）。
    if not_equals_case_sensitive(get_property(hook_input, 'hook_event_name'), 'SessionStart'):
        return 0

    # ここに到達したということはhook_event_name が大文字小文字を区別して 'SessionStart' に等しいということなので、
    # VS Code Local harness と判定できるので Session Target を Copilot に切り替えて新しいセッションで作業するよう案内する。
    print(to_compressed_json({
        'systemMessage': (
            "This session is using the VS Code Local harness. Open the Agents window, create a new session, "
            "and select Copilot as the Session Target to use this repository's Copilot CLI-compatible hooks. "
            "Continue this task in that new session."
        ),
    }))
    return 0


if __name__ == '__main__':
    sys.exit(main())
