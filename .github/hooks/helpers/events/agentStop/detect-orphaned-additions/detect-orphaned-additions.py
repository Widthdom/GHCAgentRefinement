import argparse
import io
import json
import os
import re
import runpy
import subprocess
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']


_IGNORED_REFERENCE_FILE_PATTERNS_KEY = 'ignoredReferenceFilePatterns'
_EXCLUDED_NAME_PATTERNS_KEY = 'excludedNamePatterns'
_LANGUAGES_KEY = 'languages'
_TEST_FILE_PATTERNS_KEY = 'testFilePatterns'
_GIT_TIMEOUT_SECONDS = 10
_REASON_PREFIX = 'The changes in this request add definitions that nothing uses'
_MAX_FINDINGS = 10
_NO_REFERENCES = 'no references'
_TEST_REFERENCES_ONLY = 'referenced only from tests'
_BLOCK_REASON = (
    _REASON_PREFIX + ':\n{findings}\n'
    'A definition that is never called is dead weight: it must still be read and maintained, and it often means the '
    'feature was not wired up. Call it from where it is needed, or remove it. If it is meant to be used from outside '
    'this repository (for example, a public API or an entry point a framework invokes by name), keep it and say so in '
    'the response.'
)


def _is_string_list(value, allow_empty=True):
    return (
        isinstance(value, list) and (allow_empty or bool(value))
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _load_language(definition):
    # 戻り値は (ファイルのパスの正規表現, 注釈の正規表現またはNone, 定義の正規表現のリスト)。
    if not isinstance(definition, dict) or not all(
        isinstance(definition.get(key), str) and definition[key].strip() for key in ('name', 'filePattern')
    ):
        raise ValueError(f'each item of {_LANGUAGES_KEY} requires non-empty strings for name and filePattern.')
    key = f'{_LANGUAGES_KEY}[{definition["name"]}]'
    annotation = definition.get('annotationPattern')
    if annotation is not None and not (isinstance(annotation, str) and annotation.strip()):
        raise ValueError(f'annotationPattern in {key} must be a non-empty string when present.')
    definition_patterns = definition.get('definitionPatterns')
    if not _is_string_list(definition_patterns, allow_empty=False):
        raise ValueError(f'definitionPatterns in {key} must be a non-empty array of non-empty strings.')
    # 定義は行頭から照合するため、行頭は各行に一致させる。
    compiled = _build_regex_matchers(definition_patterns, key, re.MULTILINE)
    if any('name' not in pattern.groupindex for pattern in compiled):
        raise ValueError(f'each definitionPattern in {key} requires a named group "name".')
    return (
        _build_regex_matchers([definition['filePattern']], key)[0],
        None if annotation is None else _build_regex_matchers([annotation], key)[0],
        compiled,
    )


def _load_definitions(path):
    # 定義はUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('orphaned addition definitions must be a JSON object.')
    for key in (_IGNORED_REFERENCE_FILE_PATTERNS_KEY, _EXCLUDED_NAME_PATTERNS_KEY):
        if not _is_string_list(definitions.get(key)):
            raise ValueError(f'orphaned addition definitions require an array of non-empty strings for {key}.')
    languages = definitions.get(_LANGUAGES_KEY)
    if not isinstance(languages, list) or not languages:
        raise ValueError(f'orphaned addition definitions require a non-empty array for {_LANGUAGES_KEY}.')
    return (
        _build_regex_matchers(definitions[_IGNORED_REFERENCE_FILE_PATTERNS_KEY], _IGNORED_REFERENCE_FILE_PATTERNS_KEY),
        _build_regex_matchers(definitions[_EXCLUDED_NAME_PATTERNS_KEY], _EXCLUDED_NAME_PATTERNS_KEY),
        [_load_language(language) for language in languages],
    )


def _load_test_file_patterns(path):
    # テストファイルの判定は block-test-weakening hookの定義を共有する。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict) or not _is_string_list(definitions.get(_TEST_FILE_PATTERNS_KEY), False):
        raise ValueError(f'test file definitions require a non-empty array of non-empty strings for '
                         f'{_TEST_FILE_PATTERNS_KEY}.')
    return _build_regex_matchers(definitions[_TEST_FILE_PATTERNS_KEY], _TEST_FILE_PATTERNS_KEY)


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・編集・本hookによる差し戻し済みかを集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    # 編集ツールの引数の解析は、共通utilityで行う。
    extract_edit_tool_changes = runpy.run_path(
        os.path.join(_utilities_directory, 'extract-edit-tool-changes', 'extract-edit-tool-changes.py')
    )['extract_edit_tool_changes']
    last_assistant_msg = None
    changes = []
    already_requested = False
    for event in runpy.run_path(
        os.path.join(_utilities_directory, 'read-jsonl-objects', 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        event_type = event.get('type')

        if event_type == 'user.message':
            content = data.get('content')
            if data.get('delivery') != 'queued':
                last_assistant_msg = None
                changes.clear()
                already_requested = False
            elif isinstance(content, str) and content.startswith(_REASON_PREFIX):
                already_requested = True
        elif event_type == 'assistant.message':
            content = data.get('content')
            if isinstance(content, str) and content.strip():
                last_assistant_msg = content
        elif event_type == 'tool.execution_start':
            tool_name = data.get('toolName')
            if isinstance(tool_name, str):
                changes.extend(extract_edit_tool_changes(tool_name, data.get('arguments')))
    return last_assistant_msg, changes, already_requested


def _run_git(cwd, arguments, allowed_return_codes=(0,)):
    # gitがない、リポジトリ外、または検索に失敗した場合はNoneを返す。参照を数えられないだけなので異常終了にはしない。
    try:
        result = subprocess.run(
            ['git', *arguments], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=_GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode not in allowed_return_codes:
        return None
    return result.stdout.decode('utf-8', errors='replace')


def _read_file_lines(path):
    # gitの行番号と揃えるため、`\n` だけで分ける（splitlinesは `\r` や `\x0c` でも分けてしまう）。
    try:
        with open(path, encoding='utf-8', errors='replace', newline='') as file:
            return file.read().split('\n')
    except OSError:
        return None


def _get_language(languages, path):
    for language in languages:
        if language[0].search(path):
            return language
    return None


def _find_line_number(lines, line):
    # 前後の空白を除いて一致する最初の行の番号（1始まり）を返す。
    for index, current in enumerate(lines):
        if current.strip() == line:
            return index + 1
    return None


def _is_annotated(lines, line_number, annotation_pattern):
    # 直前の空行でない行が属性・デコレーターなら、フレームワークが名前で呼ぶ定義とみなす。
    if annotation_pattern is None:
        return False
    for current in reversed(lines[:line_number - 1]):
        if current.strip():
            return bool(annotation_pattern.search(current))
    return False


def _find_candidates(changes, cwd, repository_root, excluded_name_patterns, languages, test_file_patterns):
    # 戻り値は (リポジトリルートからの相対パス, 行番号, 名前) のリスト。
    # 置換前の内容に同じ行があるもの（元からあるもの）と、最終的にファイルから消えたものは除く。
    candidates = []
    file_lines = {}
    for change in changes:
        path, written, removed = change['path'], change['written'], change['removed']
        full_path = os.path.realpath(os.path.join(cwd, path))
        try:
            if os.path.commonpath([repository_root, full_path]) != repository_root:
                continue
        except ValueError:
            # Windowsでドライブが異なる場合、commonpathはValueErrorになる。
            continue
        # パターンの照合と理由での表示は、OSによらず `/` 区切りのパスで行う。
        relative_path = os.path.relpath(full_path, repository_root).replace(os.sep, '/')
        language = _get_language(languages, relative_path)
        if language is None or any(pattern.search(relative_path) for pattern in test_file_patterns):
            continue
        removed_lines = {line.strip() for line in removed.splitlines()}
        for pattern in language[2]:
            for match in pattern.finditer(written):
                name = match.group('name')
                if any(pattern.search(name) for pattern in excluded_name_patterns):
                    continue
                if any(candidate[0] == relative_path and candidate[2] == name for candidate in candidates):
                    continue
                start = written.rfind('\n', 0, match.start()) + 1
                end = written.find('\n', match.start())
                line = written[start:None if end < 0 else end].strip()
                if line in removed_lines:
                    continue
                if full_path not in file_lines:
                    file_lines[full_path] = _read_file_lines(full_path) if os.path.isfile(full_path) else None
                lines = file_lines[full_path]
                line_number = None if lines is None else _find_line_number(lines, line)
                if line_number is None or _is_annotated(lines, line_number, language[1]):
                    continue
                candidates.append((relative_path, line_number, name))
    return candidates


def _find_orphans(candidates, repository_root, ignored_reference_file_patterns, test_file_patterns):
    # 戻り値は (相対パス, 行番号, 名前, 種類) のリスト。gitで検索できなければNoneを返す。
    names = sorted({candidate[2] for candidate in candidates})
    arguments = ['grep', '--untracked', '-I', '-n', '-w', '-F', '--null']
    for name in names:
        arguments.extend(['-e', name])
    # 一致がない場合、git grepは終了コード1を返す。
    output = _run_git(repository_root, arguments, allowed_return_codes=(0, 1))
    if output is None:
        return None
    name_patterns = {name: re.compile(r'(?<![\w$])' + re.escape(name) + r'(?![\w$])') for name in names}
    references = {name: set() for name in names}
    for record in output.splitlines():
        parts = record.split('\0', 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        path, line_number, content = parts[0], int(parts[1]), parts[2]
        if any(pattern.search(path) for pattern in ignored_reference_file_patterns):
            continue
        is_test = any(pattern.search(path) for pattern in test_file_patterns)
        for name, pattern in name_patterns.items():
            if pattern.search(content):
                references[name].add((path, line_number, is_test))
    orphans = []
    for path, line_number, name in candidates:
        # 定義した行そのものは参照に数えない。
        found = [reference for reference in references[name] if reference[:2] != (path, line_number)]
        if not found:
            orphans.append((path, line_number, name, _NO_REFERENCES))
        elif all(reference[2] for reference in found):
            orphans.append((path, line_number, name, _TEST_REFERENCES_ONLY))
    return orphans


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    script_directory = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path', default=os.path.join(script_directory, 'orphaned-addition-definitions.json'),
    )
    parser.add_argument(
        '--test-definitions-path',
        default=os.path.join(
            os.path.dirname(os.path.dirname(script_directory)), 'preToolUse', 'block-test-weakening',
            'test-weakening-definitions.json',
        ),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        ignored_reference_file_patterns, excluded_name_patterns, languages = _load_definitions(
            parsed_arguments.definitions_path
        )
        test_file_patterns = _load_test_file_patterns(parsed_arguments.test_definitions_path)
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('agentStop hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('agentStop hook input must be a JSON object.')

        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0

        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd must be a non-empty string.')
        if not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        transcript_path = hook_input.get('transcriptPath')
        if not isinstance(transcript_path, str) or not transcript_path.strip():
            raise ValueError('transcriptPath must be a non-empty string.')
        if not os.path.isfile(transcript_path):
            raise ValueError(f'transcript path is not a file or could not be verified as a file: {transcript_path}')

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答と編集を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, changes, already_requested = _get_turn_summary(transcript_path)
        # 同じ依頼で差し戻すのは1回だけにし、意図して残す場合に停止できなくなるのを防ぐ。
        if last_assistant_msg is None or already_requested or not changes:
            return 0
        # 参照はリポジトリ全体から探すため、gitがない、またはリポジトリ外の場合は検査しない。
        repository_root = _run_git(cwd, ['rev-parse', '--show-toplevel'])
        if repository_root is None or not repository_root.strip():
            return 0
        repository_root = os.path.realpath(repository_root.strip())
        candidates = _find_candidates(
            changes, cwd, repository_root, excluded_name_patterns, languages, test_file_patterns,
        )
        if not candidates:
            return 0
        orphans = _find_orphans(candidates, repository_root, ignored_reference_file_patterns, test_file_patterns)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect orphaned additions: {ex}', file=sys.stderr)
        return 2

    if orphans:
        findings = '\n'.join(
            f'- {path}:{line_number}: {name} ({kind})' for path, line_number, name, kind in orphans[:_MAX_FINDINGS]
        )
        print(json.dumps(
            {'decision': 'block', 'reason': _BLOCK_REASON.format(findings=findings)},
            ensure_ascii=False, separators=(',', ':'),
        ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
