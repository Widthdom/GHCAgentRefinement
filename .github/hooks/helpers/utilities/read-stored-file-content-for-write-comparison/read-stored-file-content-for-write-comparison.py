# ファイルに保存されている今の内容を、書き込む内容（これから書く内容・書いた内容）と比べるために読む。
# 書き込む内容が元からあるか、上書きや置換で何が増減するか、書いた内容が残っているかを調べる
# preToolUse hookとutilityが共有する。


def read_stored_file_content_for_write_comparison(path):
    # UTF-8として読めないバイトも置換文字にして読み、比較を止めない。改行コードは `\n` に揃える。
    # 内容が元のバイト列と一致しないため、比較専用とし、ファイルへの書き戻しには使わない。
    # 読めない場合（ファイルがない、ディレクトリであるなど）はNoneを返す。
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()
    except OSError:
        return None
