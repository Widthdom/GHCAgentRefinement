import re
import shlex

# bashのコマンド文字列を、論理行ごとに字句へ分ける。
# シェルコマンドの語や演算子を調べるpreToolUse hookが共有する。

# `<<<` は文字列を渡すだけなので、ヒアドキュメントとみなさない。
_HEREDOC_PATTERN = re.compile(
    r'(?<!<)<<(?!<)(-?)\s*(?:\'([^\'\n]*)\'|"([^"\n]*)"|\\?([^\s;&|()<>\'"]+))'
)
_LINE_CONTINUATION_PATTERN = re.compile(r'(?<!\\)(?:\\\\)*\\$')


def _split(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def split_bash_lines(command):
    # 論理行ごとの字句のリストを順に返す。引用符は外し、連続する演算子の文字（`;`・`&&`・`|`・`>` など）は1つの字句にする。
    # 引用符が閉じるまでと行末の `\` は次の行とつなげる。ヒアドキュメントの本文はコマンドではないため読み飛ばす。
    # 引用符が閉じないまま終わった部分は判定できないため返さない。
    heredocs = []
    buffer = ''
    for line in command.replace('\r\n', '\n').split('\n'):
        if heredocs:
            delimiter, strip_tabs = heredocs[0]
            if (line.lstrip('\t') if strip_tabs else line) == delimiter:
                heredocs.pop(0)
            continue
        buffer += line
        if _LINE_CONTINUATION_PATTERN.search(buffer):
            buffer = buffer[:-1]
            continue
        try:
            tokens = _split(buffer)
        except ValueError:
            buffer += '\n'
            continue
        yield tokens
        for match in _HEREDOC_PATTERN.finditer(buffer):
            heredocs.append((next(group for group in match.groups()[1:] if group is not None), match.group(1) == '-'))
        buffer = ''
