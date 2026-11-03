import difflib
import glob
import importlib.metadata
import importlib.util
import io
import json
import os
import re
import runpy
import sqlite3
import subprocess
import sys
from typing import cast


# ファイルに書き込む組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
# 置換前の内容を表す引数。書き込む内容ではないため検査せず、既存の内容として照合に使う。
REMOVED_TEXT_KEYS = {'old_str', 'old_string'}
# 書き込む内容ではない引数。
IGNORED_KEYS = {'path', 'command'}
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+?)\s*$')

# 依存を書くファイルの種類。ファイル名（`/` 区切りのパスの末尾）で判定する。
NUGET_MANIFEST_PATTERN = re.compile(r'(?i)\.(?:csproj|fsproj|vbproj|props|targets)$')
REQUIREMENTS_PATTERN = re.compile(r'(?i)(?:^|/)(?:requirements[^/]*\.(?:txt|in)|requirements/[^/]+\.(?:txt|in))$')
PYPROJECT_PATTERN = re.compile(r'(?:^|/)pyproject\.toml$')
PACKAGE_JSON_PATTERN = re.compile(r'(?:^|/)package\.json$')
PYTHON_SOURCE_PATTERN = re.compile(r'\.pyi?$')

NUGET_TAG_PATTERN = re.compile(r'<Package(?:Reference|Version)\b[^>]*>', re.DOTALL)
NUGET_INCLUDE_PATTERN = re.compile(r'\bInclude\s*=\s*"(?P<name>[^"$]+)"')
NUGET_VERSION_PATTERN = re.compile(r'\bVersion\s*=\s*"(?P<version>[^"]+)"')
NUGET_FIXED_VERSION_PATTERN = re.compile(r'^\d+(?:\.\d+){0,3}(?:-[0-9A-Za-z.-]+)?$')
PYPI_NAME = r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?'
REQUIREMENT_PATTERN = re.compile(rf'^\s*(?P<name>{PYPI_NAME})\s*(?:\[[^\]]*\])?\s*(?:[<>=!~;@]|$)')
PYPROJECT_ARRAY_ITEM_PATTERN = re.compile(
    rf'^\s*["\'](?P<name>{PYPI_NAME})\s*(?:\[[^\]]*\])?\s*[<>=!~][^"\']*["\']\s*,?\s*(?:#.*)?$'
)
PYPROJECT_INLINE_ARRAY_PATTERN = re.compile(r'^\s*(?:dependencies|requires|[\w-]+)\s*=\s*\[(?P<items>.*)\]\s*$')
PYPROJECT_INLINE_ARRAY_KEYS = re.compile(r'^\s*(?:dependencies|requires|dev|test|docs|[\w-]*deps?)\s*=')
PYPROJECT_QUOTED_ITEM_PATTERN = re.compile(rf'["\'](?P<name>{PYPI_NAME})\s*(?:\[[^\]]*\])?\s*(?:[<>=!~;][^"\']*)?["\']')
POETRY_DEPENDENCY_PATTERN = re.compile(
    rf'^\s*(?P<name>{PYPI_NAME})\s*=\s*(?:["\'][~^<>=!*]?\s*\d[^"\']*["\']|\{{[^}}]*\bversion\s*=)'
)
NPM_NAME = r'(?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*'
PACKAGE_JSON_ENTRY_PATTERN = re.compile(rf'^\s*"(?P<name>{NPM_NAME})"\s*:\s*"(?P<version>[^"]*)"\s*,?\s*$')
NPM_VERSION_PATTERN = re.compile(r'^(?:[~^]|[<>]=?|=)?\s*v?\d+(?:\.(?:\d+|[xX*]))*(?:[-+][\w.-]+)?(?:\s.*)?$|^(?:\*|latest|next|x)$')
# package.jsonの依存以外で、値がバージョンの形になるキー。
PACKAGE_JSON_EXCLUDED_KEYS = {'version', 'node', 'npm', 'yarn', 'pnpm', 'bun', 'deno', 'vscode'}
PYTHON_IMPORT_PATTERN = re.compile(r'^\s*import\s+(?P<modules>[A-Za-z_][\w.]*(?:\s+as\s+\w+)?(?:\s*,\s*[A-Za-z_][\w.]*(?:\s+as\s+\w+)?)*)\s*(?:#.*)?$')
PYTHON_FROM_IMPORT_PATTERN = re.compile(r'^\s*from\s+(?P<module>[A-Za-z_][\w.]*)\s+import\b')

NUGET = 'nuget'
PYPI = 'pypi'
NPM = 'npm'
PYTHON_MODULE = 'python-module'
ECOSYSTEM_LABELS = {NUGET: 'NuGet package', PYPI: 'PyPI package', NPM: 'npm package', PYTHON_MODULE: 'Python module'}
MAX_FINDINGS = 10
MAX_LISTED_FILES = 50000
CLOSE_MATCH_CUTOFF = 0.85
GIT_TIMEOUT_SECONDS = 3
# ファイル一覧の代わりに歩くとき、依存の実体や生成物しかないディレクトリは除く。
PRUNED_DIRECTORIES = {'.git', 'node_modules', '.venv', 'venv', 'bin', 'obj', '__pycache__', 'dist', 'build'}
DENY_REASON = (
    'Edit denied: could not verify that these dependencies exist: {findings}. '
    'A plausible but nonexistent package name may be registered later by an attacker (slopsquatting). '
    'Verify each one with a registry search such as `dotnet package search <id>`, `pip index versions <name>`, '
    'or `npm view <name>`, or by fetching its page on nuget.org, pypi.org, or npmjs.com, then retry the edit. '
    'For a Python module, confirm it with `python -c "import <name>"` in the project environment. '
    'Do not install a package just to check that it exists. '
    'If you cannot reach the registry, tell the user which dependency you want to add and why instead.'
)


def get_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列（apply_patch）で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def collect_strings(value, ignored_keys=frozenset()):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in ignored_keys:
                yield from collect_strings(item, ignored_keys)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item, ignored_keys)


def get_patch_writes(text):
    # パッチをファイルごとに分け、追加行（`+`）を書き込む内容、削除行（`-`）を置換前の内容とする。
    writes = []
    for line in text.splitlines():
        match = PATCH_FILE_PATTERN.match(line)
        if match:
            writes.append({'path': match.group(2), 'written': [], 'removed': [], 'deleted': match.group(1) == 'Delete'})
        elif writes and line.startswith('+'):
            writes[-1]['written'].append(line[1:])
        elif writes and line.startswith('-'):
            writes[-1]['removed'].append(line[1:])
    return [
        {'path': write['path'], 'written': '\n'.join(write['written']), 'removed': '\n'.join(write['removed'])}
        for write in writes if not write['deleted']
    ]


def get_writes(tool_name, tool_args):
    # 戻り値は {path, written（書き込む内容）, removed（置換前の内容）} のリスト。
    if tool_name == 'apply_patch':
        return [write for text in collect_strings(tool_args) for write in get_patch_writes(text)]
    if not isinstance(tool_args, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    if not isinstance(path, str) or not path.strip():
        return []
    return [{
        'path': path,
        'written': '\n'.join(collect_strings(tool_args, IGNORED_KEYS | REMOVED_TEXT_KEYS)),
        'removed': '\n'.join(text for key in REMOVED_TEXT_KEYS for text in collect_strings(tool_args.get(key))),
    }]


def read_text(path):
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as file:
            return file.read()
    except OSError:
        return ''


def get_kind(slash_path):
    # 戻り値は依存を書くファイルの種類。対象外ならNone。
    if NUGET_MANIFEST_PATTERN.search(slash_path):
        return 'nuget'
    if REQUIREMENTS_PATTERN.search(slash_path):
        return 'requirements'
    if PYPROJECT_PATTERN.search(slash_path):
        return 'pyproject'
    if PACKAGE_JSON_PATTERN.search(slash_path):
        return 'package-json'
    if PYTHON_SOURCE_PATTERN.search(slash_path):
        return 'python-source'
    return None


def extract_dependencies(kind, text):
    # 戻り値は (エコシステム, 名前, バージョン) のリスト。バージョンを照合しない場合はNone。
    dependencies = []
    if kind == 'nuget':
        for tag in NUGET_TAG_PATTERN.finditer(text):
            include = NUGET_INCLUDE_PATTERN.search(tag.group(0))
            if include:
                version = NUGET_VERSION_PATTERN.search(tag.group(0))
                dependencies.append((NUGET, include.group('name').strip(), version.group('version').strip() if version else None))
    elif kind == 'requirements':
        for line in text.splitlines():
            line = line.split('#', 1)[0]
            # オプション（`-r`・`-e`・`--index-url` など）とURL・パスの指定は除く。
            if line.strip().startswith(('-', '.', '/')) or '://' in line:
                continue
            match = REQUIREMENT_PATTERN.match(line)
            if match:
                dependencies.append((PYPI, match.group('name'), None))
    elif kind == 'pyproject':
        for line in text.splitlines():
            match = PYPROJECT_ARRAY_ITEM_PATTERN.match(line)
            if match:
                dependencies.append((PYPI, match.group('name'), None))
                continue
            if PYPROJECT_INLINE_ARRAY_KEYS.match(line):
                inline = PYPROJECT_INLINE_ARRAY_PATTERN.match(line)
                if inline:
                    dependencies.extend(
                        (PYPI, item.group('name'), None) for item in PYPROJECT_QUOTED_ITEM_PATTERN.finditer(inline.group('items'))
                    )
                    continue
            match = POETRY_DEPENDENCY_PATTERN.match(line)
            # `python = "^3.12"` や `minversion = "6.0"` などは依存ではない。
            if match and 'python' not in match.group('name').lower() and 'version' not in match.group('name').lower():
                dependencies.append((PYPI, match.group('name'), None))
    elif kind == 'package-json':
        for line in text.splitlines():
            match = PACKAGE_JSON_ENTRY_PATTERN.match(line)
            if (
                match and match.group('name') not in PACKAGE_JSON_EXCLUDED_KEYS
                and NPM_VERSION_PATTERN.match(match.group('version').strip())
            ):
                dependencies.append((NPM, match.group('name'), None))
    elif kind == 'python-source':
        for line in text.splitlines():
            match = PYTHON_IMPORT_PATTERN.match(line)
            if match:
                for module in match.group('modules').split(','):
                    dependencies.append((PYTHON_MODULE, module.split()[0].split('.')[0], None))
                continue
            match = PYTHON_FROM_IMPORT_PATTERN.match(line)
            if match:
                dependencies.append((PYTHON_MODULE, match.group('module').split('.')[0], None))
    return dependencies


def normalize_name(state, ecosystem, name):
    if ecosystem == PYTHON_MODULE:
        return name
    return state['normalize_name'](ecosystem, name)


class Environment:
    # 実在の照合先。必要になったものだけを調べ、結果を呼び出しの間だけ保持する。
    def __init__(self, cwd, root, state):
        self.cwd = cwd
        self.root = root
        self.state = state
        self.cached = {}

    def cache(self, key, factory):
        if key not in self.cached:
            self.cached[key] = factory()
        return self.cached[key]

    def list_files(self):
        # リポジトリ内のファイル（`/` 区切りのルート相対パス）。gitが使えなければディレクトリを歩く。
        return self.cache('files', self._list_files)

    def _list_files(self):
        if self.root is not None:
            try:
                result = subprocess.run(
                    ['git', '-C', self.root, 'ls-files', '-co', '--exclude-standard', '-z'],
                    capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
                )
                if result.returncode == 0:
                    return [path for path in result.stdout.decode('utf-8', errors='replace').split('\0') if path][:MAX_LISTED_FILES]
            except (OSError, subprocess.TimeoutExpired):
                pass
        base = self.root or self.cwd
        files = []
        for current, directories, filenames in os.walk(base):
            directories[:] = [directory for directory in directories if directory not in PRUNED_DIRECTORIES]
            for filename in filenames:
                files.append(os.path.relpath(os.path.join(current, filename), base).replace(os.sep, '/'))
                if len(files) >= MAX_LISTED_FILES:
                    return files
        return files

    def base(self):
        return self.root or self.cwd

    def manifest_names(self, ecosystem):
        # リポジトリ内の他の依存ファイルに既にある名前。
        return self.cache(('manifest', ecosystem), lambda: {
            normalize_name(self.state, dependency_ecosystem, name)
            for path in self.list_files()
            if (kind := get_kind(path)) and kind != 'python-source'
            for dependency_ecosystem, name, _ in extract_dependencies(kind, read_text(os.path.join(self.base(), path)))
            if dependency_ecosystem == ecosystem
        })

    def nuget_packages_directory(self):
        return os.environ.get('NUGET_PACKAGES') or os.path.join(os.path.expanduser('~'), '.nuget', 'packages')

    def nuget_cached_names(self):
        directory = self.nuget_packages_directory()
        return self.cache('nuget-cache', lambda: set(os.listdir(directory)) if os.path.isdir(directory) else set())

    def nuget_asset_names(self):
        # 復元済みのプロジェクトの `obj/project.assets.json` にあるパッケージ。
        def load():
            names = set()
            for path in self.list_files():
                if not NUGET_MANIFEST_PATTERN.search(path) or not path.lower().endswith('proj'):
                    continue
                assets = os.path.join(self.base(), os.path.dirname(path), 'obj', 'project.assets.json')
                try:
                    with open(assets, encoding='utf-8-sig') as file:
                        libraries = json.load(file).get('libraries', {})
                except (OSError, ValueError, AttributeError):
                    continue
                names.update(key.split('/')[0].lower() for key in libraries if isinstance(key, str))
            return names
        return self.cache('nuget-assets', load)

    def nuget_version_exists(self, name, version):
        directory = os.path.join(self.nuget_packages_directory(), name.lower())
        if not os.path.isdir(directory):
            return True
        versions = {entry.lower() for entry in os.listdir(directory)}
        version = version.lower()
        # NuGetは `1.0` を `1.0.0` のように3要素以上へそろえて保存する。
        core, separator, suffix = version.partition('-')
        parts = core.split('.')
        padded = '.'.join(parts + ['0'] * (3 - len(parts))) + separator + suffix
        return version in versions or padded in versions

    def site_packages_directories(self):
        # フックを動かすPythonとは別の、リポジトリ直下の仮想環境（`.venv` など）。globの `*` は `.` 始まりに一致しないため、
        # ディレクトリを列挙してから探す。
        def load():
            try:
                entries = [entry for entry in os.listdir(self.base()) if os.path.isdir(os.path.join(self.base(), entry))]
            except OSError:
                return []
            return [
                path
                for entry in entries
                for pattern in (('lib', 'python*', 'site-packages'), ('Lib', 'site-packages'))
                for path in glob.glob(os.path.join(glob.escape(os.path.join(self.base(), entry)), *pattern))
            ]
        return self.cache('site-packages', load)

    def installed_distributions(self):
        def load():
            names = {self.state['normalize_name'](PYPI, distribution.metadata['Name'] or '')
                     for distribution in importlib.metadata.distributions()}
            for directory in self.site_packages_directories():
                for entry in os.listdir(directory):
                    if entry.endswith(('.dist-info', '.egg-info')):
                        names.add(self.state['normalize_name'](PYPI, entry.rsplit('.', 1)[0].split('-')[0]))
            names.discard('')
            return names
        return self.cache('distributions', load)

    def locked_python_names(self):
        def load():
            names = set()
            for path in self.list_files():
                if os.path.basename(path) in ('uv.lock', 'poetry.lock', 'pdm.lock', 'Pipfile.lock'):
                    text = read_text(os.path.join(self.base(), path))
                    names.update(self.state['normalize_name'](PYPI, name) for name in re.findall(
                        r'(?m)^\s*"?name"?\s*[=:]\s*"([^"]+)"', text,
                    ))
            return names
        return self.cache('python-locks', load)

    def local_module_names(self):
        def load():
            names = set()
            for path in self.list_files():
                parts = path.split('/')
                for part in parts[:-1]:
                    names.add(part)
                stem = parts[-1].split('.')[0]
                if re.search(r'\.(?:py|pyi|pyd|so)$', parts[-1]):
                    names.add(stem)
            return names
        return self.cache('local-modules', load)

    def installed_module_names(self):
        def load():
            names = set(sys.stdlib_module_names) | set(sys.builtin_module_names) | {'__future__'}
            names.update(importlib.metadata.packages_distributions())
            for directory in self.site_packages_directories():
                names.update(entry.split('.')[0] for entry in os.listdir(directory))
            return names
        return self.cache('installed-modules', load)

    def module_exists(self, name):
        if name in self.installed_module_names() or name in self.local_module_names():
            return True
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    def npm_names(self, manifest_directory):
        # manifestのディレクトリからリポジトリのルートまでの `node_modules` と、ロックファイルにある名前。
        def load():
            names = set()
            directory = manifest_directory
            while True:
                node_modules = os.path.join(directory, 'node_modules')
                if os.path.isdir(node_modules):
                    for entry in os.listdir(node_modules):
                        if entry.startswith('@') and os.path.isdir(os.path.join(node_modules, entry)):
                            names.update(f'{entry}/{child}'.lower() for child in os.listdir(os.path.join(node_modules, entry)))
                        else:
                            names.add(entry.lower())
                for lock in ('package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lock'):
                    text = read_text(os.path.join(directory, lock))
                    # package-lock.jsonの `"node_modules/<名前>"`、yarn.lock・pnpm-lock.yamlの行頭の `<名前>@`。
                    names.update(name.lower() for name in re.findall(rf'node_modules/({NPM_NAME})"', text))
                    names.update(name.lower() for name in re.findall(rf'(?m)^\s*["\'/]?({NPM_NAME})@', text))
                parent = os.path.dirname(directory)
                if directory == self.base() or parent == directory or not directory.startswith(self.base()):
                    break
                directory = parent
            return names
        return self.cache(('npm', manifest_directory), load)

    def exists(self, ecosystem, name, manifest_directory):
        normalized = normalize_name(self.state, ecosystem, name)
        if ecosystem == PYTHON_MODULE:
            return self.module_exists(name)
        if normalized in self.manifest_names(ecosystem):
            return True
        if ecosystem == NUGET:
            return normalized in self.nuget_cached_names() or normalized in self.nuget_asset_names()
        if ecosystem == PYPI:
            return normalized in self.installed_distributions() or normalized in self.locked_python_names()
        return normalized in self.npm_names(manifest_directory)

    def known_names(self, ecosystem, manifest_directory):
        # 綴りの近い名前を探す候補。
        if ecosystem == NUGET:
            return self.nuget_cached_names() | self.nuget_asset_names() | self.manifest_names(NUGET)
        if ecosystem == PYPI:
            return self.installed_distributions() | self.locked_python_names() | self.manifest_names(PYPI)
        if ecosystem == NPM:
            return self.npm_names(manifest_directory) | self.manifest_names(NPM)
        return self.installed_module_names()


def get_repository_root(cwd):
    try:
        result = subprocess.run(
            ['git', '-C', cwd, 'rev-parse', '--show-toplevel'],
            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.decode('utf-8', errors='replace').strip()
    return os.path.realpath(root) if root else None


def get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path.replace(os.sep, '/')


def format_finding(environment, ecosystem, name, version, manifest_directory, version_only):
    if version_only:
        return f'{ECOSYSTEM_LABELS[ecosystem]} `{name}` version `{version}` (not in the local package cache)'
    finding = f'{ECOSYSTEM_LABELS[ecosystem]} `{name}`'
    normalized = name.lower() if ecosystem == PYTHON_MODULE else normalize_name(environment.state, ecosystem, name)
    candidates = {
        candidate.lower(): candidate for candidate in environment.known_names(ecosystem, manifest_directory)
        if candidate.lower() != normalized
    }
    matches = difflib.get_close_matches(normalized, list(candidates), n=1, cutoff=CLOSE_MATCH_CUTOFF)
    if matches:
        finding += f' (did you mean `{candidates[matches[0]]}`?)'
    return finding


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'verified-packages-state', 'verified-packages-state.py',
        ))
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd must be a non-empty string.')
        if not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        # preToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        tool_name = tool_name.removeprefix('functions.')
        if tool_name not in EDIT_TOOL_NAMES:
            return 0
        real_cwd = os.path.realpath(cwd)
        added = []
        for write in get_writes(tool_name, get_tool_args(hook_input['toolArgs'])):
            path = os.path.realpath(os.path.join(cwd, write['path']))
            kind = get_kind(path.replace(os.sep, '/'))
            if kind is None:
                continue
            # 既存の内容（置換前の内容とファイルの現在の内容）にある依存は、移動や周辺の編集とみなす。
            existing = {
                (ecosystem, normalize_name(state, ecosystem, name), version)
                for ecosystem, name, version in extract_dependencies(kind, '\n'.join([write['removed'], read_text(path)]))
            }
            existing_names = {(ecosystem, name) for ecosystem, name, _ in existing}
            for ecosystem, name, version in extract_dependencies(kind, write['written']):
                normalized = normalize_name(state, ecosystem, name)
                if (ecosystem, normalized, version) in existing:
                    continue
                dependency = (ecosystem, name, version, os.path.dirname(path), (ecosystem, normalized) in existing_names)
                if dependency not in added:
                    added.append(dependency)
        if not added:
            return 0

        verified = state['get_verified'](state_path)
        environment = Environment(real_cwd, get_repository_root(real_cwd), state)
        findings = []
        for ecosystem, name, version, manifest_directory, name_exists in added:
            if (ecosystem, normalize_name(state, ecosystem, name)) in verified:
                continue
            # 名前が既にあり、バージョンだけを変える場合は、バージョンだけを照合する。
            if not name_exists and not environment.exists(ecosystem, name, manifest_directory):
                findings.append(format_finding(environment, ecosystem, name, version, manifest_directory, False))
            elif (
                ecosystem == NUGET and version and NUGET_FIXED_VERSION_PATTERN.match(version)
                and not environment.nuget_version_exists(name, version)
            ):
                findings.append(format_finding(environment, ecosystem, name, version, manifest_directory, True))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check unverified dependencies: {ex}', file=sys.stderr)
        return 2

    if findings:
        shown = '; '.join(findings[:MAX_FINDINGS])
        if len(findings) > MAX_FINDINGS:
            shown += f'; and {len(findings) - MAX_FINDINGS} more'
        reason = DENY_REASON.format(findings=shown)
        print(reason, file=sys.stderr)
        # SDK形式のトップレベルpermissionDecision。正常な拒否なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({
            'permissionDecision': 'deny',
            'permissionDecisionReason': reason,
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
