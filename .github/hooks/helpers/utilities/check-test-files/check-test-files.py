import os
import runpy

# パスがテストファイルかを判定する。テストファイルを特別に扱うhook（block-test-weakening・block-goalpost-moving・
# block-hardcoding-for-tests・block-tautological-tests・block-time-bomb-literals・detect-orphaned-additions）が共有する。

_script_directory = os.path.dirname(os.path.abspath(__file__))
# 定義の読み込みは共通のutilityで行う。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(_script_directory)
_definitions = runpy.run_path(os.path.join(_utilities_directory, 'read-definitions', 'read-definitions.py'))

_DEFINITIONS_PATH = os.path.join(_script_directory, 'test-file-definitions.json')
_TEST_FILE_PATTERNS_KEY = 'testFilePatterns'


def load_test_file_patterns(definitions_path=_DEFINITIONS_PATH):
    # 戻り値は、is_test_fileに渡す正規表現のリスト。
    # ファイルがなければOSError、形式や正規表現が不正ならValueErrorにし、定義の読み込みエラーとして扱えるようにする。
    label = 'test file definitions'
    return _definitions['get_regex_matchers'](
        _definitions['read_definitions'](definitions_path, label), _TEST_FILE_PATTERNS_KEY, label,
    )


def is_test_file(test_file_patterns, display_path):
    # display_path：cwdやリポジトリルートからの相対パス。OSによらず `/` 区切りで渡す。
    return any(pattern.search(display_path) for pattern in test_file_patterns)
