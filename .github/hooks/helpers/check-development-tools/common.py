import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

PLATFORMS = ('Auto', 'Windows', 'MacOS', 'Linux')


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # 引数の誤りはPowerShellのパラメータ検証エラーと同様に終了コード1で終了する。
        self.print_usage(sys.stderr)
        print(f'{self.prog}: error: {message}', file=sys.stderr)
        sys.exit(1)


def _platform(value):
    # PowerShellのValidateSetと同様に大文字小文字を区別せずに検証し、渡された文字列はそのまま保持する。
    if value.lower() not in (platform.lower() for platform in PLATFORMS):
        raise argparse.ArgumentTypeError(
            f"invalid choice: '{value}' (choose from {', '.join(PLATFORMS)})"
        )
    return value


def parse_arguments(argv=None):
    parser = _ArgumentParser()
    parser.add_argument('--platform', type=_platform, default='Auto')
    parser.add_argument('--winget-command', default='winget')
    parser.add_argument('--brew-command', default='brew')
    parser.add_argument('--apt-command', default='apt-cache')
    parser.add_argument('--dnf-command', default='dnf')
    parser.add_argument('--pacman-command', default='pacman')
    return parser.parse_args(argv)


def run_checker(check):
    # 各チェッカーを単体で実行した場合は、見つかった項目を1行ずつ出力する。
    arguments = parse_arguments()
    sys.stdout.reconfigure(encoding='utf-8')
    for issue in check(**vars(arguments)):
        print(issue)


def load_checker(name):
    # ファイル名にハイフンを含むチェッカーを、同じディレクトリからモジュールとして読み込む。
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'check-{name}.py')
    spec = importlib.util.spec_from_file_location(f'check_{name}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_null_or_white_space(value):
    # .NETの[string]::IsNullOrWhiteSpace相当。Pythonのstr.isspace()は\x1c-\x1fも空白とみなすため使わない。
    return value is None or all(_is_white_space(char) for char in value)


def _is_white_space(char):
    return char in '\t\n\v\f\r\x85\xa0' or unicodedata.category(char) in ('Zs', 'Zl', 'Zp')


def trim(value):
    # .NETのString.Trim()相当。
    start = 0
    end = len(value)
    while start < end and _is_white_space(value[start]):
        start += 1
    while end > start and _is_white_space(value[end - 1]):
        end -= 1
    return value[start:end]


def to_ps_string(value):
    # PowerShellの文字列展開と同じ規則で文字列化する。
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value).replace('e', 'E')
    if isinstance(value, list):
        return ' '.join(to_ps_string(item) for item in value)
    if isinstance(value, dict):
        if not value:
            return ''
        return '@{' + '; '.join(f'{key}={to_ps_string(item)}' for key, item in value.items()) + '}'
    return str(value)


def get_property(value, name):
    # PowerShellのプロパティアクセスは大文字小文字を区別しないため、それに合わせる。
    # 配列に対するアクセスはPowerShellのメンバー列挙と同様に各要素の値を集める。
    if isinstance(value, dict):
        if name in value:
            return value[name]
        lowered_name = name.lower()
        for key, item in value.items():
            if key.lower() == lowered_name:
                return item
        return None
    if isinstance(value, list):
        items = []
        for element in value:
            if not isinstance(element, (dict, list)):
                continue
            item = get_property(element, name)
            if isinstance(element, list) and item is None:
                continue
            if isinstance(item, list):
                items.extend(item)
            else:
                items.append(item)
        if not items:
            return None
        return items[0] if len(items) == 1 else items
    return None


def get_first_item(value):
    # PowerShellの$value[0]相当。配列は先頭要素（空ならNone）、JSONオブジェクトはそれ自身を返す。
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, dict):
        return value
    if value is None:
        raise TypeError('Cannot index into a null array.')
    return None


def parse_json_enumerated(text):
    # -NoEnumerateなしのConvertFrom-Jsonと同様に、空白のみの入力はNoneとし、最外側の配列を展開する。
    if is_null_or_white_space(text):
        return None
    value = json.loads(text, strict=False)
    if isinstance(value, list):
        if not value:
            return None
        return value[0] if len(value) == 1 else value
    return value


def to_compressed_json(value):
    # ConvertTo-Json -Compressと同様に、非ASCII文字はエスケープせず1行のJSONにする。
    # ConvertTo-Jsonがエスケープする\u0085、\u2028、\u2029も同様にエスケープする。
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return text.replace('\u0085', '\\u0085').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def platform_equals(platform, expected):
    return platform.lower() == expected.lower()


def resolve_command(command):
    # PowerShellのGet-Commandと同様に、PATHや拡張子を考慮して実行ファイルのパスを解決する。
    return shutil.which(command)


def invoke_native_command(command, arguments):
    # 標準出力と標準エラーをまとめて行単位で取得する。コマンドを起動できない場合は終了コード1として扱う。
    path = resolve_command(command)
    if path is None:
        return 1, []
    try:
        completed = subprocess.run(
            [path, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError:
        return 1, []

    lines = re.split(r'\r\n|\r|\n', completed.stdout.decode('utf-8', errors='replace'))
    if lines and lines[-1] == '':
        lines.pop()
    return completed.returncode, lines


def convert_to_version(value):
    # 文字列中の最初のバージョン表記を、4要素（major, minor, build, revision）のタプルに正規化する。
    #   .NETの[version]と同様に、ASCII以外の数字や32ビット整数を超える値は無効とする。
    match = re.search(r'\d+(?:\.\d+){1,3}', value)
    if not match:
        return None

    parts = match.group(0).split('.')
    if not all(part.isascii() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if any(number > 2147483647 for number in numbers):
        return None
    while len(numbers) < 4:
        numbers.append(0)
    return tuple(numbers)


def version_less_than(left, right):
    # PowerShellの-ltと同様に、Noneはどのバージョンより小さく、None同士は等しいものとして比較する。
    if left is None:
        return right is not None
    if right is None:
        return False
    return left < right


def version_sort_key(version):
    # Sort-Objectと同様に、Noneを最小として並べ替える。
    return (version is not None, version or ())


class ToolVersion:
    def __init__(self, display, match, source, value):
        self.display = display
        self.match = match
        self.source = source
        self.value = value


class PackageVersion:
    def __init__(self, display, value, package_id=None):
        self.display = display
        self.value = value
        self.package_id = package_id


class PackageManager:
    def __init__(self, kind, command):
        self.kind = kind
        self.command = command


def get_tool_version(command, arguments, pattern):
    source = resolve_command(command)
    if source is None:
        return None

    exit_code, output = invoke_native_command(command, arguments)
    if exit_code != 0:
        return None

    match = re.search(pattern, '\n'.join(output))
    if not match:
        return None

    return ToolVersion(
        display=match.group(0),
        match=match,
        source=source,
        value=convert_to_version(match.group('version') or ''),
    )


def get_development_platform(platform='Auto'):
    if not platform_equals(platform, 'Auto'):
        return platform

    if sys.platform == 'win32':
        return 'Windows'
    if sys.platform == 'darwin':
        return 'MacOS'
    if sys.platform.startswith('linux'):
        return 'Linux'

    raise RuntimeError('Unsupported operating system.')


def get_development_package_manager(
    platform='Auto',
    winget_command='winget',
    brew_command='brew',
    apt_command='apt-cache',
    dnf_command='dnf',
    pacman_command='pacman',
):
    resolved_platform = get_development_platform(platform)
    if platform_equals(resolved_platform, 'Windows'):
        candidates = [PackageManager('winget', winget_command)]
    elif platform_equals(resolved_platform, 'MacOS'):
        candidates = [PackageManager('brew', brew_command)]
    elif platform_equals(resolved_platform, 'Linux'):
        candidates = [
            PackageManager('apt', apt_command),
            PackageManager('dnf', dnf_command),
            PackageManager('pacman', pacman_command),
        ]
    else:
        candidates = []

    for candidate in candidates:
        if resolve_command(candidate.command) is not None:
            return candidate

    return None


def get_latest_package_version(package_manager, package_id):
    if package_manager.kind == 'winget':
        return get_latest_winget_package_version(package_manager.command, package_id)

    arguments = {
        'brew': ['info', '--json=v2', package_id],
        'apt': ['policy', package_id],
        'dnf': ['--quiet', 'info', package_id],
        'pacman': ['-Si', package_id],
    }[package_manager.kind]
    exit_code, output = invoke_native_command(package_manager.command, arguments)
    if exit_code != 0:
        return None

    text = '\n'.join(output)
    display = None
    if package_manager.kind == 'brew':
        try:
            data = parse_json_enumerated(text)
            display = get_property(get_property(get_first_item(get_property(data, 'formulae')), 'versions'), 'stable')
        except Exception:
            display = None
    elif package_manager.kind == 'apt':
        match = re.search(r'(?m)^\s*Candidate:\s*(?P<version>\S+)', text)
        if match and match.group('version').lower() != '(none)':
            display = match.group('version')
    elif package_manager.kind == 'dnf':
        versions = [match.group('version') for match in re.finditer(r'(?m)^\s*Version\s*:\s*(?P<version>\S+)', text)]
        versions.sort(key=lambda version: version_sort_key(convert_to_version(version)), reverse=True)
        display = versions[0] if versions else None
    elif package_manager.kind == 'pacman':
        match = re.search(r'(?m)^\s*Version\s*:\s*(?P<version>\S+)', text)
        if match:
            display = match.group('version')

    if display is not None:
        display = to_ps_string(display)
    if is_null_or_white_space(display):
        return None

    return PackageVersion(display=display, value=convert_to_version(display))


def test_package_managed(package_manager, package_id):
    if package_manager.kind == 'winget':
        return test_winget_package_installed(package_manager.command, package_id)

    command = package_manager.command
    if package_manager.kind == 'brew':
        arguments = ['list', '--versions', package_id]
    elif package_manager.kind == 'apt':
        command = 'dpkg-query'
        arguments = ['-W', '-f=${Status}', package_id]
    elif package_manager.kind == 'dnf':
        command = 'rpm'
        arguments = ['-q', package_id]
    else:
        arguments = ['-Q', package_id]
    if resolve_command(command) is None:
        return False

    exit_code, _ = invoke_native_command(command, arguments)
    return exit_code == 0


def get_package_install_command(package_manager, package_id, upgrade=False):
    if package_manager.kind == 'winget':
        action = 'upgrade' if upgrade else 'install'
        return f'winget {action} --id {package_id} --exact --source winget'
    if package_manager.kind == 'brew':
        action = 'upgrade' if upgrade else 'install'
        return f'brew {action} {package_id}'
    if package_manager.kind == 'apt':
        if upgrade:
            return f'sudo apt-get install --only-upgrade {package_id}'
        return f'sudo apt-get install {package_id}'
    if package_manager.kind == 'dnf':
        action = 'upgrade' if upgrade else 'install'
        return f'sudo dnf {action} {package_id}'
    if package_manager.kind == 'pacman':
        return f'sudo pacman -S {package_id}'
    return None


def test_winget_output_contains_package(output, package_id):
    escaped_package_id = re.escape(package_id)
    return any(
        re.search(f'(?:^|\\s){escaped_package_id}(?:\\s|$)', line, re.IGNORECASE)
        for line in output
    )


def get_latest_winget_package_version(winget_command, package_id):
    exit_code, output = invoke_native_command(winget_command, [
        'show',
        '--id', package_id,
        '--exact',
        '--source', 'winget',
        '--versions',
        '--accept-source-agreements',
        '--disable-interactivity',
    ])
    if exit_code != 0:
        return None

    for line in output:
        candidate = trim(line)
        if not re.search(r'^\d+(?:\.\d+){1,3}$', candidate, re.IGNORECASE):
            continue

        return PackageVersion(display=candidate, value=convert_to_version(candidate))

    return None


def test_winget_package_installed(winget_command, package_id):
    exit_code, output = invoke_native_command(winget_command, [
        'list',
        '--id', package_id,
        '--exact',
        '--source', 'winget',
        '--accept-source-agreements',
        '--disable-interactivity',
    ])
    if exit_code != 0:
        return False

    return test_winget_output_contains_package(output, package_id)


def test_winget_package_update_available(winget_command, package_id):
    exit_code, output = invoke_native_command(winget_command, [
        'list',
        '--id', package_id,
        '--exact',
        '--upgrade-available',
        '--source', 'winget',
        '--accept-source-agreements',
        '--disable-interactivity',
    ])
    if exit_code != 0:
        return False

    return test_winget_output_contains_package(output, package_id)
