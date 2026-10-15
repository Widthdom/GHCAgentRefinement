import sys

from common import (
    get_development_platform,
    is_null_or_white_space,
    load_checker,
    parse_arguments,
    platform_equals,
    to_compressed_json,
)


def main():
    # 引数の検証は、PowerShell版のパラメータ検証と同様に最初に行う。
    arguments = parse_arguments()

    # 標準出力・標準エラーをUTF-8に統一することで、JSON内の日本語などを正しく出力する。BOMは出力されない。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    checkers = ['git', 'ripgrep']
    issues = []
    for checker in checkers:
        # 各チェッカーは見つかった項目を文字列のリストとして返す。
        issues.extend(load_checker(checker).check(**vars(arguments)))

    # 空白やNoneでない要素のみを残す。
    issues = [issue for issue in issues if not is_null_or_white_space(issue)]
    if len(issues) == 0:
        return 0

    resolved_platform = get_development_platform(arguments.platform)
    # get_development_platformは 公式の名称である'macOS'でなく、'MacOS'を返すため、表示用に補正する。
    platform_display_name = 'macOS' if platform_equals(resolved_platform, 'MacOS') else resolved_platform

    tool_check_report = (
        f'The {platform_display_name} development-tool check found items that need attention:\n'
        '\n'
        + '\n'.join(issues) +
        '\n'
        '\n'
        'Tell the user about these findings and show the applicable commands. '
        'Do not install or update anything automatically.'
    )

    print(to_compressed_json({'additionalContext': tool_check_report}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
