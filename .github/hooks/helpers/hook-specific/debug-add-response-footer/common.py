import codecs
import importlib.util
import json
import os
import re
import unicodedata


def test_response_footer_correction_message(content):
    # フッタ修正依頼として本hookが送ったメッセージかどうかを判定する。
    content = to_ps_string(content)
    return (
        not is_null_or_white_space(content) and
        content.startswith('The final response footer is missing or incorrect.')
    )


def load_helper(name):
    # ファイル名にハイフンを含む補助スクリプトを、同じディレクトリからモジュールとして読み込む。
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'{name}.py')
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_white_space(char):
    return char in '\t\n\v\f\r\x85\xa0' or unicodedata.category(char) in ('Zs', 'Zl', 'Zp')


def is_null_or_white_space(value):
    # .NETの[string]::IsNullOrWhiteSpace相当。文字列以外はPowerShellと同じく文字列化してから判定する。
    # Pythonのstr.isspace()は\x1c-\x1fも空白とみなすため使わない。
    return all(is_white_space(char) for char in to_ps_string(value))


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
    # 最外側の配列は展開しない。
    return json.loads(text, object_pairs_hook=build_json_object, strict=False)


def parse_json_enumerated(text):
    # -NoEnumerateなしのConvertFrom-Jsonと同様に、空白のみの入力はNoneとし、最外側の配列を展開する。
    #   要素数0はNone、1はその要素、2以上は配列として扱う。
    if is_null_or_white_space(text):
        return None
    value = parse_json(text)
    if isinstance(value, list):
        if not value:
            return None
        return value[0] if len(value) == 1 else value
    return value


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


def _equals_ignore_case(left, right):
    # PowerShellの-eqと同様に、左辺の型に合わせて右辺の文字列を変換し、大文字小文字を区別せずに比較する。
    if isinstance(left, str):
        return left.lower() == right.lower()
    if isinstance(left, bool):
        return left == (len(right) > 0)
    if isinstance(left, (int, float)):
        try:
            return left == float(right)
        except ValueError:
            return False
    return False


def equals_ignore_case(left, right):
    # PowerShellの-eqと同様に、左辺が配列の場合は条件に合う要素を抽出した結果で真偽を判定する。
    if isinstance(left, list):
        return is_truthy([item for item in left if _equals_ignore_case(item, right)])
    return _equals_ignore_case(left, right)


def not_equals_ignore_case(left, right):
    # PowerShellの-neと同様に、左辺が配列の場合は条件に合う要素を抽出した結果で真偽を判定する。
    if isinstance(left, list):
        return is_truthy([item for item in left if not _equals_ignore_case(item, right)])
    return not _equals_ignore_case(left, right)


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


def _decode_with_bom(data):
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


def read_all_text(path):
    # .NETのFile.ReadAllTextと同様にBOMから文字コードを判定し、BOMは内容に含めない。BOMがなければUTF-8として読む。
    with open(path, 'rb') as file:
        return _decode_with_bom(file.read())


def read_lines(path):
    # .NETのFile.ReadLinesと同様に、\r\n、\r、\nのいずれでも行を分割し、末尾の改行による空行は含めない。
    lines = re.split(r'\r\n|\r|\n', read_all_text(path))
    if lines and lines[-1] == '':
        lines.pop()
    return lines


def to_compressed_json(value):
    # ConvertTo-Json -Compressと同様に、非ASCII文字はエスケープせず1行のJSONにする。
    # ConvertTo-Jsonがエスケープする\u0085、\u2028、\u2029も同様にエスケープする。
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return text.replace('\u0085', '\\u0085').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
