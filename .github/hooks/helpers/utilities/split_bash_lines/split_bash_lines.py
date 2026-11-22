import re
import shlex

# bashのコマンド文字列を、論理行ごとに字句へ分ける。
# シェルコマンドの語や演算子を調べるpreToolUse hookが共有する。

_LINE_CONTINUATION_PATTERN = re.compile(r'(?<!\\)(?:\\\\)*\\$')
# 語を区切る文字。これらの直後の `#` は語の先頭になる。
_WORD_BREAK_CHARACTERS = frozenset(' \t\n;&|()<>')
_OPERATOR_CHARACTERS = frozenset(';&|()<>')


def _strip_comments(text):
    # bashでは、引用符の外で語の先頭にある `#` から行末までがコメントになる。
    # shlexは語の途中の `#` もコメントとみなすため、自前で除く。引用符が閉じない場合はそのまま返す。
    result = []
    quote = None
    index = 0
    while index < len(text):
        character = text[index]
        if quote == "'":
            if character == "'":
                quote = None
        elif quote == '"':
            if character == '\\':
                result.append(text[index:index + 2])
                index += 2
                continue
            if character == '"':
                quote = None
        elif character == '\\':
            result.append(text[index:index + 2])
            index += 2
            continue
        elif character in ('"', "'"):
            quote = character
        elif character == '#' and (index == 0 or text[index - 1] in _WORD_BREAK_CHARACTERS):
            newline_index = text.find('\n', index)
            if newline_index < 0:
                break
            index = newline_index
            continue
        result.append(character)
        index += 1
    return ''.join(result)


def _split(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    return list(lexer)


def _find_heredocs(tokens):
    # 引用符の中の `<<` は1つの語に含まれるため、演算子の字句として現れた `<<` だけをヒアドキュメントの開始とみなす。
    # `<<<` は文字列を渡すだけなので別の字句になり、対象外になる。`$((…))`・`((…))` の中の `<<` はビットシフトのため除く。
    heredocs = []
    arithmetic_depth = 0
    for index, token in enumerate(tokens):
        if token and set(token) <= _OPERATOR_CHARACTERS:
            arithmetic_depth += token.count('((')
            arithmetic_depth = max(arithmetic_depth - token.count('))'), 0)
        if token != '<<' or arithmetic_depth or index + 1 >= len(tokens):
            continue
        # `<<-` は区切りの行の先頭のタブを無視する。`-` は演算子の文字ではないため、区切り語の字句に付く。
        delimiter = tokens[index + 1]
        heredocs.append((delimiter, delimiter.startswith('-')))
    return heredocs


def split_bash_lines(command):
    # 論理行ごとの字句のリストを順に返す。引用符は外し、連続する演算子の文字（`;`・`&&`・`|`・`>` など）は1つの字句にする。
    # 引用符が閉じるまでと行末の `\` は次の行とつなげる。ヒアドキュメントの本文はコマンドではないため読み飛ばす。
    # 語の先頭の `#` から行末まではコメントとして除く。引用符が閉じないまま終わった部分は判定できないため返さない。
    heredocs = []
    buffer = ''
    for line in command.replace('\r\n', '\n').split('\n'):
        if heredocs:
            delimiter, strip_tabs = heredocs[0]
            if strip_tabs:
                delimiter = delimiter[1:]
            if (line.lstrip('\t') if strip_tabs else line) == delimiter:
                heredocs.pop(0)
            continue
        buffer += line
        stripped = _strip_comments(buffer)
        if _LINE_CONTINUATION_PATTERN.search(stripped):
            buffer = buffer[:-1]
            continue
        try:
            tokens = _split(stripped)
        except ValueError:
            buffer += '\n'
            continue
        yield tokens
        heredocs.extend(_find_heredocs(tokens))
        buffer = ''
