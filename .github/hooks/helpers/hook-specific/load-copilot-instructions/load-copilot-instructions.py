import codecs
import json
import os
import sys
import unicodedata


def is_null_or_white_space(value):
    # .NETの[string]::IsNullOrWhiteSpace相当。Pythonのstr.isspace()は\x1c-\x1fも空白とみなすため使わない。
    return all(
        char in '\t\n\v\f\r\x85\xa0' or unicodedata.category(char) in ('Zs', 'Zl', 'Zp')
        for char in value
    )


def read_all_text(path):
    # .NETのFile.ReadAllTextと同様にBOMから文字コードを判定し、BOMは内容に含めない。BOMがなければUTF-8として読む。
    with open(path, 'rb') as file:
        data = file.read()
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


def main():
    script_directory = os.path.dirname(os.path.abspath(__file__))
    instructions_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(script_directory)))),
        'copilot-instructions.md',
    )
    # ファイルとして存在するかどうかを確認する。
    if not os.path.isfile(instructions_path):
        print(f'Copilot instructions file not found: {instructions_path}', file=sys.stderr)
        return 2

    # copilot-instructions.md には日本語が含まれている可能性があるため、UTF-8で読み込む。
    instructions = read_all_text(instructions_path)
    if is_null_or_white_space(instructions):
        print(f'Copilot instructions file is empty: {instructions_path}', file=sys.stderr)
        return 2

    # 標準出力をUTF-8に統一することで、JSON内の日本語などを正しく出力する。BOMは出力されない。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # 1行のJSONにして出力する。
    output = to_compressed_json({'additionalContext': instructions})

    # 余計な出力を混ぜず、純粋な JSON 文字列を確実に渡すため、明示的に1行だけ出力する。
    print(output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
