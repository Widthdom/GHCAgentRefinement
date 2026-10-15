import re

from common import (
    get_development_package_manager,
    get_development_platform,
    get_latest_package_version,
    get_package_install_command,
    get_tool_version,
    platform_equals,
    run_checker,
    test_package_managed,
    test_winget_package_update_available,
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

    package_id = 'BurntSushi.ripgrep.MSVC' if platform_equals(resolved_platform, 'Windows') else 'ripgrep'

    installed_ripgrep_version = get_tool_version(
        'rg',
        ['--version'],
        r'ripgrep (?P<version>\d+(?:\.\d+){2,3})',
    )

    # ripgrepのVersionが取得できなかった場合の処理。
    if installed_ripgrep_version is None:
        # パッケージマネージャの有無によってインストール方法の案内を変える
        if package_manager is None:
            return [
                f'- ripgrep: not installed. No supported package manager is available for {resolved_platform}, '
                'so no install command can be suggested.'
            ]
        ripgrep_install_command = get_package_install_command(package_manager, package_id)
        return [f'- ripgrep: not installed. Install: {ripgrep_install_command}']

    # VS Code同梱のripgrepかどうかを判定するための処理。
    if re.search(r'[\\/]Microsoft VS Code Insiders[\\/]', installed_ripgrep_version.source, re.IGNORECASE):
        vs_code_package_id = 'Microsoft.VisualStudioCode.Insiders'
    elif re.search(r'[\\/]Microsoft VS Code(?:\.app)?[\\/]', installed_ripgrep_version.source, re.IGNORECASE):
        vs_code_package_id = 'Microsoft.VisualStudioCode'
    else:
        vs_code_package_id = None

    # VS Code同梱のripgrepであった場合の処理。
    if vs_code_package_id is not None:
        # Windowsでない、またはパッケージマネージャが利用できない場合は終了。
        if not platform_equals(resolved_platform, 'Windows') or package_manager is None:
            return [
                f'- ripgrep: available through VS Code ({installed_ripgrep_version.display}). '
                'Update VS Code through its built-in updater to keep the bundled version current.'
            ]

        # 以下からは、Windowsであり、かつパッケージマネージャが利用できる場合のみ実行される。
        is_vs_code_managed = test_package_managed(package_manager, vs_code_package_id)
        if (is_vs_code_managed and
                test_winget_package_update_available(winget_command, vs_code_package_id)):
            return [
                f'- ripgrep: available through VS Code ({installed_ripgrep_version.display}); '
                'a VS Code update is available. '
                f'Update: winget upgrade --id {vs_code_package_id} --exact --source winget'
            ]
        return []

    # 以下からはVS Code同梱でないripgrepであった場合のみ実行される。
    if package_manager is None:
        # パッケージマネージャが利用できない場合はここで終了する。
        return [
            f'- ripgrep: installed ({installed_ripgrep_version.display}), but the latest version could not be checked '
            f'because no supported package manager is available for {resolved_platform}.'
        ]

    # 以下からはVS Code同梱でないripgrepであり、かつパッケージマネージャが利用できる場合のみ実行される。
    latest_ripgrep_version = get_latest_package_version(package_manager, package_id)
    if latest_ripgrep_version is None:
        # 最新バージョンが取得できなかった場合はここで終了する。
        return [f'- ripgrep: installed ({installed_ripgrep_version.display}), but the latest version check failed.']

    if version_less_than(installed_ripgrep_version.value, latest_ripgrep_version.value):
        is_ripgrep_managed = test_package_managed(package_manager, package_id)
        ripgrep_install_command = get_package_install_command(
            package_manager,
            package_id,
            upgrade=is_ripgrep_managed,
        )

        return [
            f'- ripgrep: update available ({installed_ripgrep_version.display} -> '
            f'{latest_ripgrep_version.display}). Update: {ripgrep_install_command}'
        ]

    return []


if __name__ == '__main__':
    run_checker(check)
