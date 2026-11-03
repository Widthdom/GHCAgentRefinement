# Hooks used (inferred from event history)
_HOOKS_HEADER = 'Hooks used (inferred from event history)'
# Skills used (inferred from event history)
_SKILLS_HEADER = 'Skills used (inferred from event history)'
# Hooks fired (recorded by hook runner, excluding agentStop hooks)
_FIRED_HOOKS_HEADER = 'Hooks fired (recorded by hook runner, excluding agentStop hooks)'
# Current time
_TIME_HEADER = 'Current time'
_FOOTER_BOUNDARY = '\u200B'


def format_response_footer(hooks, fired_hooks, skills, current_jst):
    hook_lines = '\n'.join(f'- {hook}' for hook in hooks)
    fired_hook_lines = '\n'.join(f'- {hook}' for hook in fired_hooks)
    skill_lines = '\n'.join(f'- {skill}' for skill in skills)

    footer = (
        f'{_FOOTER_BOUNDARY}\n'
        f'## {_HOOKS_HEADER}:\n'
        f'{hook_lines}\n'
        f'## {_FIRED_HOOKS_HEADER}:\n'
        f'{fired_hook_lines}\n'
        f'## {_SKILLS_HEADER}:\n'
        f'{skill_lines}\n'
        f'## {_TIME_HEADER}\n'
        f'{current_jst}'
    )
    return footer + '\n\n'


if __name__ == '__main__':
    import argparse
    import sys

    _parser = argparse.ArgumentParser()
    _parser.add_argument('--hooks', nargs='+', required=True)
    _parser.add_argument('--fired-hooks', nargs='+', required=True)
    _parser.add_argument('--skills', nargs='+', required=True)
    _parser.add_argument('--current-jst', required=True)
    _arguments = _parser.parse_args()
    # Write bytes to preserve line endings.
    sys.stdout.buffer.write(
        format_response_footer(
            _arguments.hooks, _arguments.fired_hooks, _arguments.skills, _arguments.current_jst,
        ).encode('utf-8')
    )
