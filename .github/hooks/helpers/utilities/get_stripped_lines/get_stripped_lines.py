# 文字列の行を、前後の空白を除いた形で取り出す。
# 書き込む内容の行が既存の内容にあるか（元からあるものか）を、インデントの違いによらず照合するhookとutilityが共有する。


def get_stripped_lines(text):
    # 各行の前後の空白を除いた行の集合を返す。
    return {line.strip() for line in text.splitlines()}


def get_stripped_line_at(text, position):
    # positionの文字を含む行を、前後の空白を除いて返す。positionが改行の位置なら、その改行で終わる行を返す。
    start = text.rfind('\n', 0, position) + 1
    end = text.find('\n', position)
    return text[start:None if end < 0 else end].strip()
