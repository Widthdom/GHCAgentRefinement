import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import Mock, call, patch

script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_directory)

from common import (  # noqa: E402
    PackageManager,
    convert_to_version,
    get_development_package_manager,
    get_latest_package_version,
    get_package_install_command,
    load_checker,
)

hook_path = os.path.join(script_directory, 'check-development-tools.py')
temporary_root = os.path.join(
    tempfile.gettempdir(), 'ghc-agent-refinement', 'development-tools', str(uuid.uuid4()))
os.makedirs(temporary_root, exist_ok=True)


def new_fake_command(name, output):
    # 引数を無視して、指定した内容を標準出力に書き出すだけのコマンドを作る。
    data_path = os.path.join(temporary_root, f'{name}.txt')
    with open(data_path, 'w', encoding='utf-8', newline='\n') as file:
        file.write(output + '\n')
    if os.name == 'nt':
        path = os.path.join(temporary_root, f'{name}.cmd')
        with open(path, 'w', encoding='utf-8', newline='\r\n') as file:
            file.write(f'@type "{data_path}"\n')
    else:
        path = os.path.join(temporary_root, name)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(f"#!/bin/sh\ncat '{data_path}'\n")
        os.chmod(path, 0o755)
    return path


def new_fake_package_manager(name, output):
    return new_fake_command(name, output)


def version_to_string(version):
    return '.'.join(str(part) for part in version)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message} Expected '{expected}', got '{actual}'.")


check_development_tools = load_checker('development-tools')

try:
    version_cases = [
        {'Input': '7.6', 'Expected': '7.6.0.0'},
        {'Input': '7.6.0', 'Expected': '7.6.0.0'},
        {'Input': '7.6.6', 'Expected': '7.6.6.0'},
        {'Input': '7.6.6.0', 'Expected': '7.6.6.0'},
        {'Input': '7.6.6.1', 'Expected': '7.6.6.1'},
    ]
    for case in version_cases:
        assert_equal(
            version_to_string(convert_to_version(case['Input'])),
            case['Expected'],
            'Version normalization was incorrect.',
        )

    brew = new_fake_package_manager('brew', '{"formulae":[{"versions":{"stable":"15.2.0"}}]}')
    apt = new_fake_package_manager('apt-cache', 'ripgrep:\n  Installed: 14.1.0-1\n  Candidate: 15.1.0-1')
    dnf = new_fake_package_manager(
        'dnf',
        'Name         : ripgrep\nVersion      : 14.1.1\nName         : ripgrep\nVersion      : 15.0.0',
    )
    pacman = new_fake_package_manager(
        'pacman',
        'Repository      : extra\nName            : ripgrep\nVersion         : 15.1.0-1',
    )

    cases = [
        {'Kind': 'brew', 'Command': brew, 'Expected': '15.2.0'},
        {'Kind': 'apt', 'Command': apt, 'Expected': '15.1.0'},
        {'Kind': 'dnf', 'Command': dnf, 'Expected': '15.0.0'},
        {'Kind': 'pacman', 'Command': pacman, 'Expected': '15.1.0'},
    ]
    for case in cases:
        manager = PackageManager(case['Kind'], case['Command'])
        latest = get_latest_package_version(manager, 'ripgrep')
        if latest is None or latest.value is None:
            raise AssertionError(f"Failed to parse the {case['Kind']} package version.")
        assert_equal(
            latest.value,
            convert_to_version(case['Expected']),
            f"Failed to parse the {case['Kind']} package version.",
        )

    linux_manager = get_development_package_manager(
        platform='Linux',
        apt_command=apt,
        dnf_command=dnf,
        pacman_command=pacman,
    )
    assert_equal(linux_manager.kind, 'apt', 'Linux package manager precedence was incorrect.')

    commands = {
        'winget': 'winget upgrade --id Git.Git --exact --source winget',
        'brew': 'brew upgrade git',
        'apt': 'sudo apt-get install --only-upgrade git',
        'dnf': 'sudo dnf upgrade git',
        'pacman': 'sudo pacman -S git',
    }
    for kind, expected_command in commands.items():
        manager = PackageManager(kind, kind)
        package_id = 'Git.Git' if kind == 'winget' else 'git'
        command = get_package_install_command(manager, package_id, upgrade=True)
        assert_equal(command, expected_command, f'The {kind} update command was incorrect.')

    hook_cases_passed = 0
    for platform in ('Windows', 'MacOS', 'Linux'):
        for has_issues in (False, True):
            checkers = {
                name: Mock() for name in ('git', 'ripgrep')
            }
            issues = []
            for name, checker in checkers.items():
                checker.check.return_value = [f'- {name}: test issue'] if has_issues else []
                issues.extend(checker.check.return_value)

            with (
                patch.object(sys, 'argv', [hook_path, '--platform', platform]),
                patch.object(check_development_tools, 'load_checker', side_effect=checkers.__getitem__) as loader,
                patch('builtins.print') as output,
            ):
                assert_equal(check_development_tools.main(), 0, 'The hook did not exit successfully.')
                assert_equal(
                    loader.call_args_list,
                    [call('git'), call('ripgrep')],
                    'The hook must check only Git and ripgrep.',
                )
                for checker in checkers.values():
                    assert_equal(checker.check.call_count, 1, 'The checker was not called exactly once.')
                    assert_equal(
                        checker.check.call_args.kwargs['platform'],
                        platform,
                        'The hook did not forward the selected platform.',
                    )
                if has_issues:
                    output.assert_called_once()
                    report = json.loads(output.call_args.args[0])
                    display_platform = 'macOS' if platform == 'MacOS' else platform
                    assert_equal(
                        report,
                        {'additionalContext': (
                            f'The {display_platform} development-tool check found items that need attention:\n'
                            '\n' + '\n'.join(issues) + '\n\n'
                            'Tell the user about these findings and show the applicable commands. '
                            'Do not install or update anything automatically.'
                        )},
                        'The hook report was incorrect.',
                    )
                else:
                    output.assert_not_called()
            hook_cases_passed += 1

    completed = subprocess.run(
        [
            sys.executable, hook_path,
            '--platform', 'Linux',
            '--apt-command', apt,
            '--dnf-command', os.path.join(temporary_root, 'missing-dnf'),
            '--pacman-command', os.path.join(temporary_root, 'missing-pacman'),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        check=True,
    )
    hook_result = json.loads(completed.stdout.decode('utf-8'))
    additional_context = hook_result['additionalContext']
    if re.search('The Linux development-tool check found items that need attention:',
                 additional_context, re.IGNORECASE) is None:
        raise AssertionError('The hook did not report the selected Linux platform.')
    if re.search('sudo apt-get install', additional_context, re.IGNORECASE) is None:
        raise AssertionError('The hook did not suggest an apt command on Linux.')
    if re.search('PowerShell|Python', additional_context, re.IGNORECASE):
        raise AssertionError('The hook must not include PowerShell or Python recommendations.')

    print(f'Passed {len(version_cases) + len(cases) + len(commands) + hook_cases_passed + 2} development-tool tests.')
finally:
    if os.path.exists(temporary_root):
        shutil.rmtree(temporary_root)
