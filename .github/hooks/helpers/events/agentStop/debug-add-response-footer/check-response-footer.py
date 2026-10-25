import re
from collections import Counter
from datetime import datetime

# Match the hooks, skills, and current time headings.
FOOTER_PATTERN = (
    r'(?s)## Hooks used \(inferred from event history\):\r?\n'
    r'(?P<hooks>(?:- [^\r\n]+\r?\n)+)'
    r'## Skills used \(inferred from event history\):\r?\n'
    r'(?P<skills>(?:- [^\r\n]+\r?\n)+)'
    r'## Current time\r?\n'
    r'(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) JST\r?\n\r?\n\Z'
)


def _items_are_equal(expected, actual):
    return Counter(item.lower() for item in expected) == Counter(item.lower() for item in actual)


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

    current_timestamp = datetime.strptime(current_jst, '%Y-%m-%d %H:%M:%S JST')
    try:
        footer_timestamp = datetime.strptime(footer_match.group('timestamp'), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return False
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
