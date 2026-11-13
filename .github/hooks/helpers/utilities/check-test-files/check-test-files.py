import json
import os
import runpy

# パスがテストファイルかを判定する。テストファイルを特別に扱うhook（block-test-weakening・block-goalpost-moving・
# block-hardcoding-for-tests・block-tautological-tests・block-time-bomb-literals・detect-orphaned-additions）が共有する。

_script_directory = os.path.dirname(os.path.abspath(__file__))
# utilityのフォルダー → utilities の直下に共有の処理がある。
_utilities_directory = os.path.dirname(_script_directory)
_build_regex_matchers = runpy.run_path(os.path.join(
    _utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py',
))['build_regex_matchers']
_is_non_blank_string_list = runpy.run_path(os.path.join(
    _utilities_directory, 'check-string-list', 'check-string-list.py',
))['is_non_blank_string_list']

_DEFINITIONS_PATH = os.path.join(_script_directory, 'test-file-definitions.json')
_TEST_FILE_PATTERNS_KEY = 'testFilePatterns'


def load_test_file_patterns(definitions_path=_DEFINITIONS_PATH):
    # 戻り値は、is_test_fileに渡す正規表現のリスト。
    # ファイルがなければOSError、形式や正規表現が不正ならValueErrorにし、定義の読み込みエラーとして扱えるようにする。
    with open(definitions_path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    patterns = definitions.get(_TEST_FILE_PATTERNS_KEY) if isinstance(definitions, dict) else None
    if not _is_non_blank_string_list(patterns):
        raise ValueError(
            f'test file definitions require a non-empty array of non-empty strings for {_TEST_FILE_PATTERNS_KEY}.'
        )
    return _build_regex_matchers(patterns, _TEST_FILE_PATTERNS_KEY)


def is_test_file(test_file_patterns, display_path):
    # display_path：cwdやリポジトリルートからの相対パス。OSによらず `/` 区切りで渡す。
    return any(pattern.search(display_path) for pattern in test_file_patterns)
