import os
import re
import sys
import tempfile
import unittest

_script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_directory)
import read_definitions as _utility
_read_definitions = _utility.read_definitions
_is_non_blank_string_list = _utility.is_non_blank_string_list
_build_regex_matcher = _utility.build_regex_matcher
_build_regex_matchers = _utility.build_regex_matchers

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests

_LABEL = 'sample definitions'


class ReadDefinitionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-read-definitions-test-')
        self.addCleanup(temporary.cleanup)
        self.__directory = temporary.name
        self.__path = os.path.join(temporary.name, 'definitions.json')

    def __write(self, content, encoding='utf-8'):
        with open(self.__path, 'w', encoding=encoding, newline='') as file:
            file.write(content)

    def test_reads_object_and_array_with_or_without_bom(self):
        for encoding in ('utf-8', 'utf-8-sig'):
            with self.subTest(encoding=encoding):
                self.__write('{"key":"日本語"}', encoding)
                self.assertEqual(_read_definitions(self.__path, _LABEL), {'key': '日本語'})
                self.__write('[{"key":1}]', encoding)
                self.assertEqual(_read_definitions(self.__path, _LABEL, list), [{'key': 1}])

    def test_rejects_invalid_content(self):
        for content, expected_type, message in (
            ('{', dict, f'{_LABEL} are not valid JSON:'),
            ('[]', dict, f'{_LABEL} must be a JSON object.'),
            ('{}', list, f'{_LABEL} must be a non-empty JSON array.'),
            ('[]', list, f'{_LABEL} must be a non-empty JSON array.'),
        ):
            with self.subTest(content=content, expected_type=expected_type):
                self.__write(content)
                with self.assertRaises(ValueError) as context:
                    _read_definitions(self.__path, _LABEL, expected_type)
                self.assertIn(message, str(context.exception))
                # JSONの解析の例外は連鎖させず、定義の読み込みエラーとして示す。
                self.assertIsNone(context.exception.__cause__)

    def test_rejects_missing_file_and_directory(self):
        for path in (self.__path, self.__directory):
            with self.subTest(path=path):
                with self.assertRaisesRegex(OSError, f'^{re.escape(_LABEL)} path is not a file'):
                    _read_definitions(path, _LABEL)


class ValueTests(unittest.TestCase):
    def test_is_non_blank_string_list(self):
        for value, expected, expected_when_empty_allowed in (
            (['a', ' b ', '日本語'], True, True),
            ([], False, True),
            # 空文字列や空白だけの要素は、allow_empty_listの指定によらず認めない。
            ([''], False, False),
            ([' \t\n'], False, False),
            (['a', 1], False, False),
            ([['a']], False, False),
            (('a',), False, False),
            ('a', False, False),
            (None, False, False),
        ):
            with self.subTest(value=value):
                self.assertIs(_is_non_blank_string_list(value), expected)
                self.assertIs(_is_non_blank_string_list(value, allow_empty_list=True), expected_when_empty_allowed)

    def test_build_regex_matcher(self):
        matcher = _build_regex_matcher(r'^b$', 'key', re.MULTILINE)
        self.assertEqual(matcher.pattern, r'^b$')
        self.assertIsNotNone(matcher.search('a\nb'))
        with self.assertRaises(ValueError) as context:
            _build_regex_matcher(r'(', 'testFilePattern')
        # 場所と理由に続けて、パターンの文字列を示す。
        self.assertRegex(str(context.exception), r"^invalid regular expression in testFilePattern: .+ \(pattern: '\('\)$")
        # 元の例外は連鎖させず、定義の読み込みエラーとして示す。
        self.assertIsNone(context.exception.__cause__)

    def test_build_regex_matchers(self):
        matchers = _build_regex_matchers([r'^a', r'^b$'], 'key', re.MULTILINE)
        self.assertEqual([matcher.pattern for matcher in matchers], [r'^a', r'^b$'])
        self.assertIsNotNone(matchers[1].search('a\nb'))
        self.assertEqual(_build_regex_matchers([], 'key'), [])
        with self.assertRaises(ValueError) as context:
            _build_regex_matchers([r'a', r'('], 'testFilePatterns')
        # 場所には、リストでの添字（0始まり）を添える。
        self.assertRegex(
            str(context.exception), r"^invalid regular expression in testFilePatterns \(index 1\): .+ \(pattern: '\('\)$"
        )
        # 元の例外は連鎖させず、定義の読み込みエラーとして示す。
        self.assertIsNone(context.exception.__cause__)

    def test_get_string_list_and_regex_matchers(self):
        definitions = {'words': ['a', 'b'], 'empty': [], 'patterns': [r'^x$'], 'invalid': ['(']}
        self.assertEqual(_utility.get_string_list(definitions, 'words', _LABEL), ['a', 'b'])
        self.assertEqual(_utility.get_string_list(definitions, 'empty', _LABEL, allow_empty_list=True), [])
        patterns = _utility.get_regex_matchers(definitions, 'patterns', _LABEL, flags=re.MULTILINE)
        self.assertIsNotNone(patterns[0].search('y\nx'))
        for key, allow_empty_list, message in (
            ('empty', False, f'empty in {_LABEL} must be a non-empty array of non-blank strings.'),
            ('missing', True, f'missing in {_LABEL} must be an array of non-blank strings.'),
            ('invalid', False, f'invalid regular expression in invalid in {_LABEL} (index 0):'),
        ):
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as context:
                    _utility.get_regex_matchers(definitions, key, _LABEL, allow_empty_list)
                self.assertIn(message, str(context.exception))

    def test_get_regex_matcher(self):
        self.assertEqual(_utility.get_regex_matcher({'pattern': r'\d'}, 'pattern', _LABEL).pattern, r'\d')
        for value, message in (
            (None, f'pattern in {_LABEL} must be a non-blank string.'),
            (' ', f'pattern in {_LABEL} must be a non-blank string.'),
            (['a'], f'pattern in {_LABEL} must be a non-blank string.'),
            ('(', f'invalid regular expression in pattern in {_LABEL}:'),
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as context:
                    _utility.get_regex_matcher({'pattern': value}, 'pattern', _LABEL)
                self.assertIn(message, str(context.exception))

    def test_named_regex_matchers(self):
        entries = [{'name': 'digit', 'pattern': r'^\d$', 'extra': True}, {'name': 'word', 'pattern': r'\w'}]
        named = _utility.get_named_regex_matchers({'entries': entries}, 'entries', _LABEL, flags=re.MULTILINE)
        self.assertEqual([(name, pattern.pattern) for name, pattern in named], [('digit', r'^\d$'), ('word', r'\w')])
        self.assertIsNotNone(named[0][1].search('a\n1'))
        location = f'entries in {_LABEL}'
        for value, message in (
            ([], f'{location} must be a non-empty array of objects with non-blank strings for name and pattern.'),
            ([{'name': 'a'}], f'{location} must be a non-empty array of objects'),
            ([{'name': ' ', 'pattern': 'a'}], f'{location} must be a non-empty array of objects'),
            (['a'], f'{location} must be a non-empty array of objects'),
            (None, f'{location} must be a non-empty array of objects'),
            # 不正な正規表現は、名前を添えて示す。
            ([{'name': 'bad', 'pattern': '('}], f'invalid regular expression in {location} (bad):'),
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as context:
                    _utility.get_named_regex_matchers({'entries': value}, 'entries', _LABEL)
                self.assertIn(message, str(context.exception))
        # 最上位の配列は、build_named_regex_matchersに場所を示して渡す。
        self.assertEqual(_utility.build_named_regex_matchers(entries[:1], _LABEL)[0][0], 'digit')


if __name__ == '__main__':
    _run_tests()
