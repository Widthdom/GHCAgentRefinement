import re
import sys
from collections import Counter
from datetime import datetime

# 使用したhook（イベント履歴から推定）, 使用したskill（イベント履歴から推定）, 現在時刻, の各見出しに一致する。
FOOTER_PATTERN = (
    r'(?s)## \u4F7F\u7528\u3057\u305Fhook\uFF08\u30A4\u30D9\u30F3\u30C8\u5C65\u6B74\u304B\u3089\u63A8\u5B9A\uFF09:\r?\n'
    r'(?P<hooks>(?:- [^\r\n]+\r?\n)+)'
    r'## \u4F7F\u7528\u3057\u305Fskill\uFF08\u30A4\u30D9\u30F3\u30C8\u5C65\u6B74\u304B\u3089\u63A8\u5B9A\uFF09:\r?\n'
    r'(?P<skills>(?:- [^\r\n]+\r?\n)+)'
    r'## \u73FE\u5728\u6642\u523B\r?\n'
    r'(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) JST\r?\n\r?\n\Z'
)


def _parse_timestamp(value):
    # .NETのDateTime.ParseExact(value, 'yyyy-MM-dd HH:mm:ss', InvariantCulture)と同様に、
    # ASCII数字で書式どおりに書かれた実在する日時だけを受け付ける。
    match = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})', value, re.ASCII)
    if not match:
        raise ValueError(f"String '{value}' was not recognized as a valid DateTime.")
    return datetime(*(int(group) for group in match.groups()))


def _items_are_equal(expected, actual):
    # Compare-Objectと同様に、大文字小文字を区別せず、順序を問わずに比較する。
    return (
        len(actual) == len(expected) and
        Counter(item.lower() for item in expected) == Counter(item.lower() for item in actual)
    )


def test_response_footer(response, expected_hooks, expected_skills, current_jst):
    footer_match = re.search(FOOTER_PATTERN, response)
    if not footer_match:
        return False

    footer_hooks = [
        match.group('hook')
        for match in re.finditer(r'(?m)^- (?P<hook>[^\r\n]+)\r?$', footer_match.group('hooks'))
    ]
    hooks_are_correct = _items_are_equal(expected_hooks, footer_hooks)

    footer_skills = [
        match.group('skill')
        for match in re.finditer(r'(?m)^- (?P<skill>[^\r\n]+)\r?$', footer_match.group('skills'))
    ]
    skills_are_correct = _items_are_equal(expected_skills, footer_skills)

    current_timestamp = _parse_timestamp(re.sub(r' JST$', '', current_jst, flags=re.IGNORECASE))
    footer_timestamp = _parse_timestamp(footer_match.group('timestamp'))
    age_minutes = (current_timestamp - footer_timestamp).total_seconds() / 60
    timestamp_is_current = -2 <= age_minutes <= 10

    return hooks_are_correct and skills_are_correct and timestamp_is_current


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--response', required=True)
    parser.add_argument('--expected-hooks', nargs='+', required=True)
    parser.add_argument('--expected-skills', nargs='+', required=True)
    parser.add_argument('--current-jst', required=True)
    arguments = parser.parse_args()
    print(test_response_footer(
        arguments.response,
        arguments.expected_hooks,
        arguments.expected_skills,
        arguments.current_jst,
    ))
