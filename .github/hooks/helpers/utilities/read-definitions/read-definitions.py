import json
import os
import re

# hookの定義ファイル（JSON）を読み、値を検証して使える形にする。定義ファイルを読むhookとutilityが共有する。
# ファイルがなければOSError、形式や正規表現が不正ならValueErrorにし、呼び出し元のhookが定義の読み込みエラーとして扱えるようにする。
# label：エラーに示す定義の名前（'mojibake definitions' など）。key：定義ファイルでのキー。
#   キーの値のエラーは `<key> in <label>` を場所として示す。


def read_definitions(path, label, expected_type=dict):
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


def build_regex_matchers(patterns, location, flags=0):
    # patterns：正規表現の文字列のリスト。location：不正な正規表現の場所としてエラーに示す文字列。
    # 戻り値は、patternsと同じ順のre.Patternのリスト。
    try:
        return [re.compile(pattern, flags) for pattern in patterns]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {location}: {ex}') from None


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
    return build_regex_matchers([value], f'{key} in {label}', flags)[0]


def _has_name_and_pattern(entry):
    # entryが、`name` と `pattern` に空白だけでない文字列を持つオブジェクトかを返す。
    return isinstance(entry, dict) and all(
        isinstance(entry.get(key), str) and entry[key].strip() for key in ('name', 'pattern')
    )


def build_named_regex_matchers(entries, location, flags=0):
    # entries：`name` と `pattern` を持つオブジェクトの空でないリスト。location：entriesの場所としてエラーに示す文字列。
    # 戻り値は (名前, re.Pattern) のリスト。不正な正規表現は、名前を添えて示す。
    if not isinstance(entries, list) or not entries or not all(map(_has_name_and_pattern, entries)):
        raise ValueError(f'{location} must be a non-empty array of objects with non-blank strings for name and pattern.')
    return [
        (entry['name'], build_regex_matchers([entry['pattern']], f"{location} ({entry['name']})", flags)[0])
        for entry in entries
    ]


def get_named_regex_matchers(definitions, key, label, flags=0):
    # キーの値（`name` と `pattern` を持つオブジェクトのリスト）を検証し、(名前, re.Pattern) のリストにして返す。
    return build_named_regex_matchers(definitions.get(key), f'{key} in {label}', flags)
