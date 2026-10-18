import os

# 使用したhook（イベント履歴から推定）
HOOKS_HEADER = '\u4F7F\u7528\u3057\u305Fhook\uFF08\u30A4\u30D9\u30F3\u30C8\u5C65\u6B74\u304B\u3089\u63A8\u5B9A\uFF09'
# 使用したskill（イベント履歴から推定）
SKILLS_HEADER = '\u4F7F\u7528\u3057\u305Fskill\uFF08\u30A4\u30D9\u30F3\u30C8\u5C65\u6B74\u304B\u3089\u63A8\u5B9A\uFF09'
# 現在時刻
TIME_HEADER = '\u73FE\u5728\u6642\u523B'
FOOTER_BOUNDARY = '\u200B'


def format_response_footer(hooks, skills, current_jst):
    # 項目の連結と末尾の空行にはOS標準の改行を使い、見出しなどの固定部分の改行は\nとする。
    hook_lines = os.linesep.join(f'- {hook}' for hook in hooks)
    skill_lines = os.linesep.join(f'- {skill}' for skill in skills)

    footer = (
        f'{FOOTER_BOUNDARY}\n'
        f'## {HOOKS_HEADER}:\n'
        f'{hook_lines}\n'
        f'## {SKILLS_HEADER}:\n'
        f'{skill_lines}\n'
        f'## {TIME_HEADER}\n'
        f'{current_jst}'
    )
    return footer + os.linesep * 2


if __name__ == '__main__':
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument('--hooks', nargs='+', required=True)
    parser.add_argument('--skills', nargs='+', required=True)
    parser.add_argument('--current-jst', required=True)
    arguments = parser.parse_args()
    # 改行コードを変換せずに出力するため、バイト列として書き込む。
    sys.stdout.buffer.write(
        format_response_footer(arguments.hooks, arguments.skills, arguments.current_jst).encode('utf-8')
    )
