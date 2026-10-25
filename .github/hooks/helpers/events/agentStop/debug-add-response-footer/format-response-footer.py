# Hooks used (inferred from event history)
HOOKS_HEADER = 'Hooks used (inferred from event history)'
# Skills used (inferred from event history)
SKILLS_HEADER = 'Skills used (inferred from event history)'
# Current time
TIME_HEADER = 'Current time'
FOOTER_BOUNDARY = '\u200B'


def format_response_footer(hooks, skills, current_jst):
    hook_lines = '\n'.join(f'- {hook}' for hook in hooks)
    skill_lines = '\n'.join(f'- {skill}' for skill in skills)

    footer = (
        f'{FOOTER_BOUNDARY}\n'
        f'## {HOOKS_HEADER}:\n'
        f'{hook_lines}\n'
        f'## {SKILLS_HEADER}:\n'
        f'{skill_lines}\n'
        f'## {TIME_HEADER}\n'
        f'{current_jst}'
    )
    return footer + '\n\n'


if __name__ == '__main__':
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument('--hooks', nargs='+', required=True)
    parser.add_argument('--skills', nargs='+', required=True)
    parser.add_argument('--current-jst', required=True)
    arguments = parser.parse_args()
    # Write bytes to preserve line endings.
    sys.stdout.buffer.write(
        format_response_footer(arguments.hooks, arguments.skills, arguments.current_jst).encode('utf-8')
    )
