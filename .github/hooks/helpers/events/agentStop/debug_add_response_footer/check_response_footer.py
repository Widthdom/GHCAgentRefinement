import re
from collections import Counter
from datetime import datetime

# Match the hooks, fired hooks, skills, and current time headings.
_FOOTER_PATTERN = (
    r'(?s)## Hooks used \(inferred from event history\):\r?\n'
    r'(?P<hooks>(?:- [^\r\n]+\r?\n)+)'
    r'## Hooks fired \(recorded by hook runner, excluding agentStop hooks\):\r?\n'
    r'(?P<fired_hooks>(?:- [^\r\n]+\r?\n)+)'
    r'## Skills used \(inferred from event history\):\r?\n'
    r'(?P<skills>(?:- [^\r\n]+\r?\n)+)'
    r'## Current time\r?\n'
    r'(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) JST\r?\n\r?\n\Z'
)


def _items_are_equal(expected, actual):
    return Counter(item.lower() for item in expected) == Counter(item.lower() for item in actual)


def _get_items(footer_match, group_name):
    return [
        match.group('item')
        for match in re.finditer(r'(?m)^- (?P<item>[^\r\n]+)\r?$', footer_match.group(group_name))
    ]


def is_valid_response_footer(response, expected_hooks, expected_fired_hooks, expected_skills, current_jst):
    footer_match = re.search(_FOOTER_PATTERN, response)
    if not footer_match:
        return False

    hooks_are_correct = _items_are_equal(expected_hooks, _get_items(footer_match, 'hooks'))
    fired_hooks_are_correct = _items_are_equal(expected_fired_hooks, _get_items(footer_match, 'fired_hooks'))
    skills_are_correct = _items_are_equal(expected_skills, _get_items(footer_match, 'skills'))

    current_timestamp = datetime.strptime(current_jst, '%Y-%m-%d %H:%M:%S JST')
    try:
        footer_timestamp = datetime.strptime(footer_match.group('timestamp'), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return False
    age_minutes = (current_timestamp - footer_timestamp).total_seconds() / 60
    timestamp_is_current = -2 <= age_minutes <= 10

    return hooks_are_correct and fired_hooks_are_correct and skills_are_correct and timestamp_is_current
