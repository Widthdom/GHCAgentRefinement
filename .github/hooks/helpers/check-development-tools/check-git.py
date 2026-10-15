from common import (
    convert_to_version,
    get_development_package_manager,
    get_development_platform,
    get_latest_package_version,
    get_package_install_command,
    get_tool_version,
    platform_equals,
    run_checker,
    test_package_managed,
    version_less_than,
)


def check(
    platform='Auto',
    winget_command='winget',
    brew_command='brew',
    apt_command='apt-cache',
    dnf_command='dnf',
    pacman_command='pacman',
):
    resolved_platform = get_development_platform(platform)

    package_manager = get_development_package_manager(
        platform=platform,
        winget_command=winget_command,
        brew_command=brew_command,
        apt_command=apt_command,
        dnf_command=dnf_command,
        pacman_command=pacman_command,
    )

    package_id = 'Git.Git' if platform_equals(resolved_platform, 'Windows') else 'git'

    installed_git_version = get_tool_version(
        'git',
        ['--version'],
        r'git version (?P<version>\d+(?:\.\d+){2})(?:\.windows\.(?P<revision>\d+))?',
    )

    # GitのVersionが取得できなかった場合の処理。
    if installed_git_version is None:
        # パッケージマネージャの利用可否によってインストール方法の案内を変える。
        if package_manager is None:
            return [
                f'- Git: not installed. No supported package manager is available for {resolved_platform}, '
                'so no install command can be suggested.'
            ]
        git_install_command = get_package_install_command(package_manager, package_id)
        return [f'- Git: not installed. Install: {git_install_command}']

    # 取得したGitのVersionを正規化し、revisionが含まれている場合はversionに統合する
    if installed_git_version.match.group('revision') is not None:
        installed_git_version.value = convert_to_version(
            f"{installed_git_version.match.group('version')}.{installed_git_version.match.group('revision')}"
        )

    # GitはインストールされていてVersionも取得できているが、パッケージマネージャが利用できない場合の処理。
    # パッケージマネージャが利用できないとこの先の最新バージョンの確認ができないためここでreturnする。
    if package_manager is None:
        return [
            f'- Git: installed ({installed_git_version.display}), but the latest version could not be checked '
            f'because no supported package manager is available for {resolved_platform}.'
        ]

    latest = get_latest_package_version(package_manager, package_id)

    # パッケージマネージャによる最新バージョンが取得できなかった場合の処理。
    if latest is None:
        return [f'- Git: installed ({installed_git_version.display}), but the latest version check failed.']

    if version_less_than(installed_git_version.value, latest.value):
        is_git_managed = test_package_managed(package_manager, package_id)
        git_install_command = get_package_install_command(package_manager, package_id, upgrade=is_git_managed)

        return [
            f'- Git: update available ({installed_git_version.display} -> {latest.display}). '
            f'Update: {git_install_command}'
        ]

    return []


if __name__ == '__main__':
    run_checker(check)
