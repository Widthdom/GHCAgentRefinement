import re

# 定義ファイルに書かれた正規表現の文字列から、照合に使うマッチャー（re.Pattern）を作る。
# 正規表現の定義を読み込むhookが共有する。


def build_regex_matchers(patterns, key, flags=0):
    # patterns：正規表現の文字列のリスト。key：定義ファイルでのキー。不正な正規表現の場所としてエラーに示す。
    # 戻り値は、patternsと同じ順のre.Patternのリスト。
    # 作る過程で不正な正規表現が見つかればValueErrorにし、定義の読み込みエラーとして扱えるようにする。
    try:
        return [re.compile(pattern, flags) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {key}: {ex}') from None
