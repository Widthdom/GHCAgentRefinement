import json
import os
import re
from typing import Any

# hookの定義ファイル（JSON）を読み、値を検証して使える形にする。定義ファイルを読むhookとutilityが共有する。
# ファイルがなければOSError、形式や正規表現が不正ならValueErrorにし、呼び出し元のhookが定義の読み込みエラーとして扱えるようにする。
# label：エラーに示す定義の名前（'mojibake definitions' など）。key：定義ファイルでのキー。
#   キーの値のエラーは `<key> in <label>` を場所として示す。


# expected_typeには既定値のdictとlistのどちらも渡せるよう、型注釈で明示する（注釈がないと型チェッカーが既定値から `type[dict]` と推論し、listを渡すと型エラーになる）。
# 戻り値の形は呼び出し元が指定したexpected_typeで決まるため、Anyとして返す。
def read_definitions(path, label, expected_type: type[dict] | type[list] = dict) -> Any:
    # UTF-8（BOMは許容する）のJSONを読み、最上位がexpected_type（dict：オブジェクト、list：空でない配列）であれば返す。
    if not os.path.isfile(path):
        raise FileNotFoundError(f'{label} path is not a file or could not be verified as a file: {path}')
    try:
        with open(path, encoding='utf-8-sig') as file:
            definitions = json.load(file)
    except json.JSONDecodeError as ex:
        raise ValueError(f'{label} are not valid JSON: {ex}') from None
    if expected_type is list:
        if not isinstance(definitions, list) or not definitions:
            raise ValueError(f'{label} must be a non-empty JSON array.')
    elif not isinstance(definitions, dict):
        raise ValueError(f'{label} must be a JSON object.')
    return definitions


def is_non_blank_string_list(value, allow_empty_list=False):
    # 値が、空白以外を含む文字列（non-blank）だけのリストかを判定する。
    # allow_empty_list：要素のない空のリストを認めるか。空文字列や空白だけの要素は、この指定によらず認めない。
    return (
        isinstance(value, list) and (allow_empty_list or bool(value))
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def build_regex_matcher(pattern, pattern_location, flags=0):
    # pattern：正規表現の文字列。pattern_location：定義の中でのpatternの場所としてエラーに示す文字列
    #   （'filePattern in exclusion definitions' など）。
    # 戻り値はre.Pattern。
    try:
        return re.compile(pattern, flags)
    except re.PatternError as ex:
        # re.PatternErrorはValueErrorのサブクラスではないため、定義の読み込みエラーとして扱うValueErrorに変換する。
        # re.PatternErrorのメッセージには定義の場所もパターンの文字列も含まれないため、両方を添える。
        raise ValueError(f'invalid regular expression in {pattern_location}: {ex} (pattern: {pattern!r})') from None


def build_regex_matchers(patterns, patterns_location, flags=0):
    # patterns：正規表現の文字列のリスト。patterns_location：定義の中でのpatternsの場所としてエラーに示す文字列。
    #   不正な正規表現は、patterns_locationにリストでの添字（0始まり）を添えて示す。
    # 戻り値は、patternsと同じ順のre.Patternのリスト。
    return [
        build_regex_matcher(pattern, f'{patterns_location} (index {index})', flags)
        for index, pattern in enumerate(patterns)
    ]


def get_string_list(definitions, key, label, allow_empty_list=False):
    # キーの値が空白以外を含む文字列だけのリストであれば返す。
    value = definitions.get(key)
    if not is_non_blank_string_list(value, allow_empty_list):
        array = 'an array' if allow_empty_list else 'a non-empty array'
        raise ValueError(f'{key} in {label} must be {array} of non-blank strings.')
    return value


def get_regex_matchers(definitions, key, label, allow_empty_list=False, flags=0):
    # キーの値（正規表現の文字列のリスト）を検証し、re.Patternのリストにして返す。
    return build_regex_matchers(get_string_list(definitions, key, label, allow_empty_list), f'{key} in {label}', flags)


def get_regex_matcher(definitions, key, label, flags=0):
    # キーの値（正規表現の文字列1つ）を検証し、re.Patternにして返す。
    value = definitions.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{key} in {label} must be a non-blank string.')
    return build_regex_matcher(value, f'{key} in {label}', flags)


def _has_name_and_pattern(entry):
    # entryが、`name` と `pattern` に空白だけでない文字列を持つオブジェクトかを返す。
    return isinstance(entry, dict) and all(
        isinstance(entry.get(key), str) and entry[key].strip() for key in ('name', 'pattern')
    )


def build_named_regex_matchers(entries, entries_location, flags=0):
    # entries：`name` と `pattern` を持つオブジェクトの空でないリスト。entries_location：定義の中でのentriesの場所としてエラーに示す文字列。
    # 戻り値は (名前, re.Pattern) のリスト。不正な正規表現は、entries_locationに名前を添えて示す。
    if not isinstance(entries, list) or not entries or not all(map(_has_name_and_pattern, entries)):
        raise ValueError(f'{entries_location} must be a non-empty array of objects with non-blank strings for name and pattern.')
    return [
        (
            entry['name'],
            build_regex_matcher(entry['pattern'], f"{entries_location} ({entry['name']})", flags),
        )
        for entry in entries
    ]


def get_named_regex_matchers(definitions, key, label, flags=0):
    # キーの値（`name` と `pattern` を持つオブジェクトのリスト）を検証し、(名前, re.Pattern) のリストにして返す。
    return build_named_regex_matchers(definitions.get(key), f'{key} in {label}', flags)
