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
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))


# 依存を書くファイルの種類。ファイル名（`/` 区切りのパスの末尾）で判定する。
_NUGET_MANIFEST_PATTERN = re.compile(r'(?i)\.(?:csproj|fsproj|vbproj|props|targets)$')
_REQUIREMENTS_PATTERN = re.compile(r'(?i)(?:^|/)(?:requirements[^/]*\.(?:txt|in)|requirements/[^/]+\.(?:txt|in))$')
_PYPROJECT_PATTERN = re.compile(r'(?:^|/)pyproject\.toml$')
_PACKAGE_JSON_PATTERN = re.compile(r'(?:^|/)package\.json$')
_PYTHON_SOURCE_PATTERN = re.compile(r'\.pyi?$')

_NUGET_TAG_PATTERN = re.compile(r'<Package(?:Reference|Version)\b[^>]*>', re.DOTALL)
_NUGET_INCLUDE_PATTERN = re.compile(r'\bInclude\s*=\s*"(?P<name>[^"$]+)"')
_NUGET_VERSION_PATTERN = re.compile(r'\bVersion\s*=\s*"(?P<version>[^"]+)"')
_NUGET_FIXED_VERSION_PATTERN = re.compile(r'^\d+(?:\.\d+){0,3}(?:-[0-9A-Za-z.-]+)?$')
_PYPI_NAME = r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?'
_REQUIREMENT_PATTERN = re.compile(rf'^\s*(?P<name>{_PYPI_NAME})\s*(?:\[[^\]]*\])?\s*(?:[<>=!~;@]|$)')
_PYPROJECT_ARRAY_ITEM_PATTERN = re.compile(
    rf'^\s*["\'](?P<name>{_PYPI_NAME})\s*(?:\[[^\]]*\])?\s*[<>=!~][^"\']*["\']\s*,?\s*(?:#.*)?$'
)
_PYPROJECT_INLINE_ARRAY_PATTERN = re.compile(r'^\s*(?:dependencies|requires|[\w-]+)\s*=\s*\[(?P<items>.*)\]\s*$')
_PYPROJECT_INLINE_ARRAY_KEYS = re.compile(r'^\s*(?:dependencies|requires|dev|test|docs|[\w-]*deps?)\s*=')
_PYPROJECT_QUOTED_ITEM_PATTERN = re.compile(rf'["\'](?P<name>{_PYPI_NAME})\s*(?:\[[^\]]*\])?\s*(?:[<>=!~;][^"\']*)?["\']')
_POETRY_DEPENDENCY_PATTERN = re.compile(
    rf'^\s*(?P<name>{_PYPI_NAME})\s*=\s*(?:["\'][~^<>=!*]?\s*\d[^"\']*["\']|\{{[^}}]*\bversion\s*=)'
)
_NPM_NAME = r'(?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*'
_PACKAGE_JSON_ENTRY_PATTERN = re.compile(rf'^\s*"(?P<name>{_NPM_NAME})"\s*:\s*"(?P<version>[^"]*)"\s*,?\s*$')
_NPM_VERSION_PATTERN = re.compile(r'^(?:[~^]|[<>]=?|=)?\s*v?\d+(?:\.(?:\d+|[xX*]))*(?:[-+][\w.-]+)?(?:\s.*)?$|^(?:\*|latest|next|x)$')
# package.jsonの依存以外で、値がバージョンの形になるキー。
_PACKAGE_JSON_EXCLUDED_KEYS = {'version', 'node', 'npm', 'yarn', 'pnpm', 'bun', 'deno', 'vscode'}
_PYTHON_IMPORT_PATTERN = re.compile(r'^\s*import\s+(?P<modules>[A-Za-z_][\w.]*(?:\s+as\s+\w+)?(?:\s*,\s*[A-Za-z_][\w.]*(?:\s+as\s+\w+)?)*)\s*(?:#.*)?$')
_PYTHON_FROM_IMPORT_PATTERN = re.compile(r'^\s*from\s+(?P<module>[A-Za-z_][\w.]*)\s+import\b')

_NUGET = 'nuget'
_PYPI = 'pypi'
_NPM = 'npm'
_PYTHON_MODULE = 'python-module'
_ECOSYSTEM_LABELS = {_NUGET: 'NuGet package', _PYPI: 'PyPI package', _NPM: 'npm package', _PYTHON_MODULE: 'Python module'}
_MAX_FINDINGS = 10
_MAX_LISTED_FILES = 50000
_CLOSE_MATCH_CUTOFF = 0.85
_GIT_TIMEOUT_SECONDS = 3
# ファイル一覧の代わりに歩くとき、依存の実体や生成物しかないディレクトリは除く。
_PRUNED_DIRECTORIES = {'.git', 'node_modules', '.venv', 'venv', 'bin', 'obj', '__pycache__', 'dist', 'build'}
_DENY_REASON = (
    'Edit denied: could not verify that these dependencies exist: {findings}. '
    'A plausible but nonexistent package name may be registered later by an attacker (slopsquatting). '
    'Verify each one with a registry search such as `dotnet package search <id>`, `pip index versions <name>`, '
    'or `npm view <name>`, or by fetching its page on nuget.org, pypi.org, or npmjs.com, then retry the edit. '
    'For a Python module, confirm it with `python -c "import <name>"` in the project environment. '
    'Do not install a package just to check that it exists. '
    'If you cannot reach the registry, tell the user which dependency you want to add and why instead.'
)


def _read_text(path):
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as file:
            return file.read()
    except OSError:
        return ''


def _get_kind(slash_path):
    # 戻り値は依存を書くファイルの種類。対象外ならNone。
    if _NUGET_MANIFEST_PATTERN.search(slash_path):
        return 'nuget'
    if _REQUIREMENTS_PATTERN.search(slash_path):
        return 'requirements'
    if _PYPROJECT_PATTERN.search(slash_path):
        return 'pyproject'
    if _PACKAGE_JSON_PATTERN.search(slash_path):
        return 'package-json'
    if _PYTHON_SOURCE_PATTERN.search(slash_path):
        return 'python-source'
    return None


def _extract_dependencies(kind, text):
    # 戻り値は (エコシステム, 名前, バージョン) のリスト。バージョンを照合しない場合はNone。
    dependencies = []
    if kind == 'nuget':
        for tag in _NUGET_TAG_PATTERN.finditer(text):
            include = _NUGET_INCLUDE_PATTERN.search(tag.group(0))
            if include:
                version = _NUGET_VERSION_PATTERN.search(tag.group(0))
                dependencies.append((_NUGET, include.group('name').strip(), version.group('version').strip() if version else None))
    elif kind == 'requirements':
        for line in text.splitlines():
            line = line.split('#', 1)[0]
            # オプション（`-r`・`-e`・`--index-url` など）とURL・パスの指定は除く。
            if line.strip().startswith(('-', '.', '/')) or '://' in line:
                continue
            match = _REQUIREMENT_PATTERN.match(line)
            if match:
                dependencies.append((_PYPI, match.group('name'), None))
    elif kind == 'pyproject':
        for line in text.splitlines():
            match = _PYPROJECT_ARRAY_ITEM_PATTERN.match(line)
            if match:
                dependencies.append((_PYPI, match.group('name'), None))
                continue
            if _PYPROJECT_INLINE_ARRAY_KEYS.match(line):
                inline = _PYPROJECT_INLINE_ARRAY_PATTERN.match(line)
                if inline:
                    dependencies.extend(
                        (_PYPI, item.group('name'), None) for item in _PYPROJECT_QUOTED_ITEM_PATTERN.finditer(inline.group('items'))
                    )
                    continue
            match = _POETRY_DEPENDENCY_PATTERN.match(line)
            # `python = "^3.12"` や `minversion = "6.0"` などは依存ではない。
            if match and 'python' not in match.group('name').lower() and 'version' not in match.group('name').lower():
                dependencies.append((_PYPI, match.group('name'), None))
    elif kind == 'package-json':
        for line in text.splitlines():
            match = _PACKAGE_JSON_ENTRY_PATTERN.match(line)
            if (
                match and match.group('name') not in _PACKAGE_JSON_EXCLUDED_KEYS
                and _NPM_VERSION_PATTERN.match(match.group('version').strip())
            ):
                dependencies.append((_NPM, match.group('name'), None))
    elif kind == 'python-source':
        for line in text.splitlines():
            match = _PYTHON_IMPORT_PATTERN.match(line)
            if match:
                for module in match.group('modules').split(','):
                    dependencies.append((_PYTHON_MODULE, module.split()[0].split('.')[0], None))
                continue
            match = _PYTHON_FROM_IMPORT_PATTERN.match(line)
            if match:
                dependencies.append((_PYTHON_MODULE, match.group('module').split('.')[0], None))
    return dependencies


def _normalize_name(state, ecosystem, name):
    if ecosystem == _PYTHON_MODULE:
        return name
    return state['normalize_name'](ecosystem, name)


class _Environment:
    # 実在の照合先。必要になったものだけを調べ、結果を呼び出しの間だけ保持する。
    def __init__(self, cwd, root, state):
        self.__cwd = cwd
        self.__root = root
        self.state = state
        self.__cached = {}

    def __cache(self, key, factory):
        if key not in self.__cached:
            self.__cached[key] = factory()
        return self.__cached[key]

    def __list_files(self):
        # リポジトリ内のファイル（`/` 区切りのルート相対パス）。gitが使えなければディレクトリを歩く。
        return self.__cache('files', self.__list_files_uncached)

    def __list_files_uncached(self):
        output = None if self.__root is None else _git['get_git_output'](
            self.__root, ['ls-files', '-co', '--exclude-standard', '-z'], _GIT_TIMEOUT_SECONDS,
        )
        if output is not None:
            return [path for path in output.split('\0') if path][:_MAX_LISTED_FILES]
        base = self.__root or self.__cwd
        files = []
        for current, directories, filenames in os.walk(base):
            directories[:] = [directory for directory in directories if directory not in _PRUNED_DIRECTORIES]
            for filename in filenames:
                files.append(os.path.relpath(os.path.join(current, filename), base).replace(os.sep, '/'))
                if len(files) >= _MAX_LISTED_FILES:
                    return files
        return files

    def __base(self):
        return self.__root or self.__cwd

    def __manifest_names(self, ecosystem):
        # リポジトリ内の他の依存ファイルに既にある名前。
        return self.__cache(('manifest', ecosystem), lambda: {
            _normalize_name(self.state, dependency_ecosystem, name)
            for path in self.__list_files()
            if (kind := _get_kind(path)) and kind != 'python-source'
            for dependency_ecosystem, name, _ in _extract_dependencies(kind, _read_text(os.path.join(self.__base(), path)))
            if dependency_ecosystem == ecosystem
        })

    def __nuget_packages_directory(self):
        return os.environ.get('NUGET_PACKAGES') or os.path.join(os.path.expanduser('~'), '.nuget', 'packages')

    def __nuget_cached_names(self):
        directory = self.__nuget_packages_directory()
        return self.__cache('nuget-cache', lambda: set(os.listdir(directory)) if os.path.isdir(directory) else set())

    def __nuget_asset_names(self):
        # 復元済みのプロジェクトの `obj/project.assets.json` にあるパッケージ。
        def load():
            names = set()
            for path in self.__list_files():
                if not _NUGET_MANIFEST_PATTERN.search(path) or not path.lower().endswith('proj'):
                    continue
                assets = os.path.join(self.__base(), os.path.dirname(path), 'obj', 'project.assets.json')
                try:
                    with open(assets, encoding='utf-8-sig') as file:
                        libraries = json.load(file).get('libraries', {})
                except (OSError, ValueError, AttributeError):
                    continue
                names.update(key.split('/')[0].lower() for key in libraries if isinstance(key, str))
            return names
        return self.__cache('nuget-assets', load)

    def nuget_version_exists(self, name, version):
        directory = os.path.join(self.__nuget_packages_directory(), name.lower())
        if not os.path.isdir(directory):
            return True
        versions = {entry.lower() for entry in os.listdir(directory)}
        version = version.lower()
        # NuGetは `1.0` を `1.0.0` のように3要素以上へそろえて保存する。
        core, separator, suffix = version.partition('-')
        parts = core.split('.')
        padded = '.'.join(parts + ['0'] * (3 - len(parts))) + separator + suffix
        return version in versions or padded in versions

    def __site_packages_directories(self):
        # フックを動かすPythonとは別の、リポジトリ直下の仮想環境（`.venv` など）。globの `*` は `.` 始まりに一致しないため、
        # ディレクトリを列挙してから探す。
        def load():
            try:
                entries = [entry for entry in os.listdir(self.__base()) if os.path.isdir(os.path.join(self.__base(), entry))]
            except OSError:
                return []
            return [
                path
                for entry in entries
                for pattern in (('lib', 'python*', 'site-packages'), ('Lib', 'site-packages'))
                for path in glob.glob(os.path.join(glob.escape(os.path.join(self.__base(), entry)), *pattern))
            ]
        return self.__cache('site-packages', load)

    def __installed_distributions(self):
        def load():
            names = {self.state['normalize_name'](_PYPI, distribution.metadata['Name'] or '')
                     for distribution in importlib.metadata.distributions()}
            for directory in self.__site_packages_directories():
                for entry in os.listdir(directory):
                    if entry.endswith(('.dist-info', '.egg-info')):
                        names.add(self.state['normalize_name'](_PYPI, entry.rsplit('.', 1)[0].split('-')[0]))
            names.discard('')
            return names
        return self.__cache('distributions', load)

    def __locked_python_names(self):
        def load():
            names = set()
            for path in self.__list_files():
                if os.path.basename(path) in ('uv.lock', 'poetry.lock', 'pdm.lock', 'Pipfile.lock'):
                    text = _read_text(os.path.join(self.__base(), path))
                    names.update(self.state['normalize_name'](_PYPI, name) for name in re.findall(
                        r'(?m)^\s*"?name"?\s*[=:]\s*"([^"]+)"', text,
                    ))
            return names
        return self.__cache('python-locks', load)

    def __local_module_names(self):
        def load():
            names = set()
            for path in self.__list_files():
                parts = path.split('/')
                for part in parts[:-1]:
                    names.add(part)
                stem = parts[-1].split('.')[0]
                if re.search(r'\.(?:py|pyi|pyd|so)$', parts[-1]):
                    names.add(stem)
            return names
        return self.__cache('local-modules', load)

    def __installed_module_names(self):
        def load():
            names = set(sys.stdlib_module_names) | set(sys.builtin_module_names) | {'__future__'}
            names.update(importlib.metadata.packages_distributions())
            for directory in self.__site_packages_directories():
                names.update(entry.split('.')[0] for entry in os.listdir(directory))
            return names
        return self.__cache('installed-modules', load)

    def __module_exists(self, name):
        if name in self.__installed_module_names() or name in self.__local_module_names():
            return True
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    def __npm_names(self, manifest_directory):
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
                    text = _read_text(os.path.join(directory, lock))
                    # package-lock.jsonの `"node_modules/<名前>"`、yarn.lock・pnpm-lock.yamlの行頭の `<名前>@`。
                    names.update(name.lower() for name in re.findall(rf'node_modules/({_NPM_NAME})"', text))
                    names.update(name.lower() for name in re.findall(rf'(?m)^\s*["\'/]?({_NPM_NAME})@', text))
                parent = os.path.dirname(directory)
                if directory == self.__base() or parent == directory or not directory.startswith(self.__base()):
                    break
                directory = parent
            return names
        return self.__cache(('npm', manifest_directory), load)

    def exists(self, ecosystem, name, manifest_directory):
        normalized = _normalize_name(self.state, ecosystem, name)
        if ecosystem == _PYTHON_MODULE:
            return self.__module_exists(name)
        if normalized in self.__manifest_names(ecosystem):
            return True
        if ecosystem == _NUGET:
            return normalized in self.__nuget_cached_names() or normalized in self.__nuget_asset_names()
        if ecosystem == _PYPI:
            return normalized in self.__installed_distributions() or normalized in self.__locked_python_names()
        return normalized in self.__npm_names(manifest_directory)

    def known_names(self, ecosystem, manifest_directory):
        # 綴りの近い名前を探す候補。
        if ecosystem == _NUGET:
            return self.__nuget_cached_names() | self.__nuget_asset_names() | self.__manifest_names(_NUGET)
        if ecosystem == _PYPI:
            return self.__installed_distributions() | self.__locked_python_names() | self.__manifest_names(_PYPI)
        if ecosystem == _NPM:
            return self.__npm_names(manifest_directory) | self.__manifest_names(_NPM)
        return self.__installed_module_names()


def _format_finding(environment, ecosystem, name, version, manifest_directory, version_only):
    if version_only:
        return f'{_ECOSYSTEM_LABELS[ecosystem]} `{name}` version `{version}` (not in the local package cache)'
    finding = f'{_ECOSYSTEM_LABELS[ecosystem]} `{name}`'
    normalized = name.lower() if ecosystem == _PYTHON_MODULE else _normalize_name(environment.state, ecosystem, name)
    candidates = {
        candidate.lower(): candidate for candidate in environment.known_names(ecosystem, manifest_directory)
        if candidate.lower() != normalized
    }
    matches = difflib.get_close_matches(normalized, list(candidates), n=1, cutoff=_CLOSE_MATCH_CUTOFF)
    if matches:
        finding += f' (did you mean `{candidates[matches[0]]}`?)'
    return finding


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        state = runpy.run_path(os.path.join(
            _utilities_directory, 'verified-packages-state', 'verified-packages-state.py',
        ))
        state_path = state['get_state_path'](hook_input.get('sessionId'))
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(os.path.join(
            _utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py',
        ))['extract_edit_tool_changes']
        real_cwd = os.path.realpath(cwd)
        added = []
        for change in extract_edit_tool_changes(tool_name, tool_args):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            kind = _get_kind(path.replace(os.sep, '/'))
            if kind is None:
                continue
            # 既存の内容（置換前の内容とファイルの現在の内容）にある依存は、移動や周辺の編集とみなす。
            existing = {
                (ecosystem, _normalize_name(state, ecosystem, name), version)
                for ecosystem, name, version in _extract_dependencies(kind, '\n'.join([change['removed'], _read_text(path)]))
            }
            existing_names = {(ecosystem, name) for ecosystem, name, _ in existing}
            for ecosystem, name, version in _extract_dependencies(kind, change['written']):
                normalized = _normalize_name(state, ecosystem, name)
                if (ecosystem, normalized, version) in existing:
                    continue
                dependency = (ecosystem, name, version, os.path.dirname(path), (ecosystem, normalized) in existing_names)
                if dependency not in added:
                    added.append(dependency)
        if not added:
            return 0

        verified = state['get_verified'](state_path)
        repository_root = _git['get_repository_root'](real_cwd, _GIT_TIMEOUT_SECONDS)
        if repository_root is not None:
            repository_root = os.path.realpath(repository_root)
        environment = _Environment(real_cwd, repository_root, state)
        findings = []
        for ecosystem, name, version, manifest_directory, name_exists in added:
            if (ecosystem, _normalize_name(state, ecosystem, name)) in verified:
                continue
            # 名前が既にあり、バージョンだけを変える場合は、バージョンだけを照合する。
            if not name_exists and not environment.exists(ecosystem, name, manifest_directory):
                findings.append(_format_finding(environment, ecosystem, name, version, manifest_directory, False))
            elif (
                ecosystem == _NUGET and version and _NUGET_FIXED_VERSION_PATTERN.match(version)
                and not environment.nuget_version_exists(name, version)
            ):
                findings.append(_format_finding(environment, ecosystem, name, version, manifest_directory, True))
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check unverified dependencies: {ex}', file=sys.stderr)
        return 2

    if findings:
        shown = '; '.join(findings[:_MAX_FINDINGS])
        if len(findings) > _MAX_FINDINGS:
            shown += f'; and {len(findings) - _MAX_FINDINGS} more'
        reason = _DENY_REASON.format(findings=shown)
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output['write_deny_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
