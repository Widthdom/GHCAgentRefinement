import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone


def is_null_or_white_space(value):
    # .NETの[string]::IsNullOrWhiteSpace相当。Pythonのstr.isspace()は\x1c-\x1fも空白とみなすため使わない。
    text = to_ps_string(value)
    return all(is_white_space(char) for char in text)


def is_white_space(char):
    return char in '\t\n\v\f\r\x85\xa0' or unicodedata.category(char) in ('Zs', 'Zl', 'Zp')


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


def try_parse_guid(value):
    # .NETの[System.Guid]::TryParse相当。D、N、B、P、Xの各形式を受け付け、ハイフン区切りの小文字（D形式）で返す。
    #   uuid.UUIDは.NETより緩く解釈するため使わない。
    if value is None:
        return None
    text = to_ps_string(value)
    start = 0
    end = len(text)
    while start < end and is_white_space(text[start]):
        start += 1
    while end > start and is_white_space(text[end - 1]):
        end -= 1
    text = text[start:end]

    hex_digit = '[0-9A-Fa-f]'
    d_format = f'({hex_digit}{{8}})-({hex_digit}{{4}})-({hex_digit}{{4}})-({hex_digit}{{4}})-({hex_digit}{{12}})'
    n_format = f'({hex_digit}{{8}})({hex_digit}{{4}})({hex_digit}{{4}})({hex_digit}{{4}})({hex_digit}{{12}})'
    for pattern in (d_format, n_format, r'\{' + d_format + r'\}', r'\(' + d_format + r'\)'):
        match = re.fullmatch(pattern, text)
        if match:
            return '-'.join(match.groups()).lower()

    compact = ''.join(char for char in text if not is_white_space(char))
    hex_value = f'0[xX]({hex_digit}{{1,%d}})'
    x_format = (
        r'\{' + hex_value % 8 + ',' + hex_value % 4 + ',' + hex_value % 4 + r',\{' +
        ','.join([hex_value % 2] * 8) + r'\}\}'
    )
    match = re.fullmatch(x_format, compact)
    if match:
        groups = match.groups()
        return (
            f'{int(groups[0], 16):08x}-{int(groups[1], 16):04x}-{int(groups[2], 16):04x}-'
            + ''.join(f'{int(group, 16):02x}' for group in groups[3:5])
            + '-'
            + ''.join(f'{int(group, 16):02x}' for group in groups[5:])
        )
    return None


def to_compressed_json(value):
    # ConvertTo-Json -Compressと同様に、非ASCII文字はエスケープせず1行のJSONにする。
    # ConvertTo-Jsonがエスケープする\u0085、\u2028、\u2029も同様にエスケープする。
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return text.replace('\u0085', '\\u0085').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def write_error(message):
    print(message, file=sys.stderr)


def main():
    # 標準出力・標準エラーをUTF-8に統一することで、JSON内の日本語などを正しく出力する。BOMは出力されない。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    # userPromptSubmittedのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    #   改行コードを変換せずに扱うため、バイト列として読み取ってからUTF-8として復号化する。
    input_json = sys.stdin.buffer.read().decode('utf-8', errors='replace')
    if is_null_or_white_space(input_json):
        write_error('userPromptSubmitted hook input is empty.')
        return 2

    # 読み取ったJSONをPythonのオブジェクトに変換する。
    #   最外側が[]のものは拒否したいので、配列は展開せずそのまま保持する。
    try:
        hook_input = parse_json(input_json)
    except Exception as exception:
        write_error(f'Failed to parse userPromptSubmitted hook input: {exception}')
        return 2

    # 変換結果がJSONオブジェクトでなければ異常終了。
    #   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
    if not isinstance(hook_input, dict):
        write_error('userPromptSubmitted hook input must be a JSON object.')
        return 2

    prompt = to_ps_string(get_property(hook_input, 'prompt'))
    if is_null_or_white_space(prompt):
        write_error('userPromptSubmitted hook input does not contain a prompt.')
        return 2

    session_id = get_property(hook_input, 'sessionId')
    parsed_session_id = try_parse_guid(session_id)
    if parsed_session_id is None:
        write_error(f'Invalid session ID: {to_ps_string(session_id)}')
        return 2

    # パス区切りはOS依存のため、os.path.joinで階層ごとに連結する。
    state_directory = os.path.join(os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement'), 'debug-mode')
    # 状態ファイルを格納するディレクトリを作成する。存在しない場合は新規作成。
    os.makedirs(state_directory, exist_ok=True)
    # 状態ファイルのパスを作成する。ファイル名にはハイフン区切りの標準的なGUID文字列形式のセッションIDを使用する。
    state_path = os.path.join(state_directory, f'{parsed_session_id}.json')
    # デバッグモードが有効か否か、最終更新日時をまとめ、1行のJSON形式に変換する。
    # デバッグモードが有効となるための条件は、プロンプトが'??'で始まることである。
    #   最終更新日時は.NETのラウンドトリップ形式（小数部7桁）に合わせる。
    updated_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f') + '0+00:00'
    state = to_compressed_json({
        'updatedAt': updated_at,
        'enabled': prompt.startswith('??'),
    })

    # 状態ファイルにデバッグモードの情報をBOMなしUTF-8で書き込む。
    with open(state_path, 'w', encoding='utf-8', newline='') as state_file:
        state_file.write(state)
    return 0


if __name__ == '__main__':
    sys.exit(main())
