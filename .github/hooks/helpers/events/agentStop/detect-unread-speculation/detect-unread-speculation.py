import argparse
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
_is_non_blank_string_list = runpy.run_path(os.path.join(
    _utilities_directory, 'check-string-list', 'check-string-list.py',
))['is_non_blank_string_list']
_tool_kinds = runpy.run_path(os.path.join(_utilities_directory, 'check-tool-kinds', 'check-tool-kinds.py'))
_build_regex_matchers = runpy.run_path(os.path.join(_utilities_directory, 'build-regex-matchers', 'build-regex-matchers.py'))['build_regex_matchers']
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']


# 定義ファイルのキー。speculationPatternsは推測とみなす表現の正規表現。
_SPECULATION_PATTERNS_KEY = 'speculationPatterns'
# 引数にパスが含まれていれば、そのファイルを読んだとみなすツール。`cat`・`sed -n`・`rg <pattern> <file>` などでの閲覧はrecord-seen-filesが記録しないため。
_COMMAND_READ_TOOL_NAMES = {*_tool_kinds['SHELL_TOOL_NAMES'], 'rg', 'grep'}
_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
_INLINE_CODE_PATTERN = re.compile(r'`([^`\n]+)`')
_MARKDOWN_LINK_PATTERN = re.compile(r'\]\(([^()\s]+)\)')
# コードスパン外のパス。日本語の文中に続けて書かれても切り出せるよう、ASCIIの文字だけで構成し、区切りと拡張子を必須にする。
_BARE_PATH_PATTERN = re.compile(r'(?<![A-Za-z0-9_./\\~-])((?:\.{1,2}/|/|~/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9]+)')
# 文の区切り。句点・感嘆符・疑問符の後と、改行、英文のピリオドに続く空白で区切る。
_SENTENCE_BOUNDARY_PATTERN = re.compile(r'(?<=[。！？])|\n|(?<=[.!?])\s+')
# 末尾の `:12`・`:12-34`・`#L12`・`#L12-L34` は行番号として除く。
_LINE_SUFFIX_PATTERN = re.compile(r'(?::\d+(?:-\d+)?|#L\d+(?:-L?\d+)?)$')
# プレースホルダ・glob・コマンドなど、実在するファイルを指さない表記に含まれる文字。
_NON_PATH_CHARACTERS = re.compile(r'[\s<>{}*?\[\]|$%=@,;"\'()!^&+]')
_EXTENSION_PATTERN = re.compile(r'\.[A-Za-z0-9]+$')
_MAX_REPORTED_FILES = 10
_GIT_TIMEOUT_SECONDS = 5
_REASON_PREFIX = 'The final response speculates about files'


def _load_definitions(definitions_path):
    # 定義ファイルはUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    if not os.path.isfile(definitions_path):
        raise ValueError(f'Definitions path is not a file or could not be verified as a file: {definitions_path}')
    try:
        with open(definitions_path, encoding='utf-8-sig') as file:
            definitions = json.load(file)
    except json.JSONDecodeError as ex:
        raise ValueError(f'Definitions are not valid JSON: {ex}') from ex
    if not isinstance(definitions, dict):
        raise ValueError('Definitions must be a JSON object.')
    if not _is_non_blank_string_list(definitions.get(_SPECULATION_PATTERNS_KEY)):
        raise ValueError(f'Definitions require a non-empty array of non-empty strings for {_SPECULATION_PATTERNS_KEY}.')
    return tuple(_build_regex_matchers(definitions[_SPECULATION_PATTERNS_KEY], _SPECULATION_PATTERNS_KEY))


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の最終応答と本hookによる差し戻し済みか、セッション全体でコマンド・検索ツールに渡した文字列を集める。
    turns = _turn_events['get_turns'](transcript_path)
    command_texts = [
        text
        for events in turns
        for event_type, data in events
        if event_type == 'tool.execution_start' and isinstance(data.get('toolName'), str)
        and data['toolName'].removeprefix('functions.') in _COMMAND_READ_TOOL_NAMES
        for text in _collect_json_string_values(data.get('arguments'))
    ]
    return (
        _turn_events['get_last_assistant_message'](turns[-1]),
        _turn_events['is_already_requested'](turns[-1], _REASON_PREFIX),
        command_texts,
    )


def _get_sentences(response):
    # コードブロック内の出力例やコマンドは対象外とする。
    text = _FENCED_CODE_PATTERN.sub('', response)
    return [sentence for sentence in _SENTENCE_BOUNDARY_PATTERN.split(text) if sentence and sentence.strip()]


def _get_candidates(sentence):
    # コードスパン・Markdownリンクの参照先・地の文のパスを、書かれた表記のまま返す。
    candidates = _INLINE_CODE_PATTERN.findall(sentence) + _MARKDOWN_LINK_PATTERN.findall(sentence)
    candidates += [
        re.sub(r'\.+$', '', match) for match in _BARE_PATH_PATTERN.findall(_INLINE_CODE_PATTERN.sub(' ', sentence))
    ]
    references = []
    for candidate in candidates:
        candidate = _LINE_SUFFIX_PATTERN.sub('', candidate.strip())
        if '://' in candidate or _NON_PATH_CHARACTERS.search(candidate):
            continue
        # 区切りも拡張子もない表記（`origin` など）はファイルを指さないとみなす。
        if '/' not in candidate and '\\' not in candidate and not _EXTENSION_PATTERN.search(candidate):
            continue
        references.append(candidate)
    return references


def _get_speculative_references(response, speculation_patterns):
    # 推測の表現と同じ文に書かれたパスを、出現順に重複なく返す。推測の判定にはコードスパンの中身を使わない。
    references = {}
    for sentence in _get_sentences(response):
        prose = _INLINE_CODE_PATTERN.sub(' ', sentence)
        if any(pattern.search(prose) for pattern in speculation_patterns):
            for candidate in _get_candidates(sentence):
                references.setdefault(candidate, None)
    return list(references)


def _resolve_file(candidate, cwd, repository_root):
    # 応答ではリポジトリルート基準のパスも使うため、cwdとリポジトリルートの両方を基準に解決する。
    # 実在しないパスは対象外とする（誤参照はdetect-phantom-referencesが扱う）。
    expanded = os.path.expanduser(candidate)
    for base in dict.fromkeys(filter(None, (cwd, repository_root))):
        full_path = os.path.join(base, expanded)
        if os.path.isfile(full_path):
            return full_path
    return None


def _get_seen_keys(state, state_path):
    # record-seen-filesが閲覧・編集を記録したファイルのキー。状態がなければ何も読んでいない。
    if not os.path.isfile(state_path):
        return set()
    with state['open_state'](state_path) as connection:
        return {row[0] for row in connection.execute('SELECT file FROM files WHERE state = ?', (state['SEEN'],))}


def _is_read_by_command(full_path, candidate, cwd, command_texts):
    # コマンドや検索ツールの引数に、書かれた表記・cwd相対・絶対パスのいずれかが含まれていれば読んだとみなす。
    spellings = {candidate, os.path.abspath(full_path)}
    # Windowsでドライブが異なる場合、relpathはValueErrorになるため相対表記は使わない。
    try:
        spellings.add(os.path.relpath(full_path, cwd))
    except ValueError:
        pass
    # Windowsでは `/` 区切りでも同じパスを書けるため、OS依存の区切りの表記を `/` 区切りにしたものも加える。
    spellings |= {spelling.replace(os.sep, '/') for spelling in spellings}
    return any(spelling in text for text in command_texts for spelling in spellings)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        dest='defs_path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'unread-speculation-definitions.json'),
    )
    parsed_args = parser.parse_args()

    try:
        speculation_patterns = _load_definitions(parsed_args.defs_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to load unread speculation definitions: {ex}', file=sys.stderr)
        return 2

    try:
        hook_input = _hook_input['read_hook_input']()
        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0
        transcript_path = _hook_input['get_transcript_path'](hook_input)
        cwd = _hook_input['get_cwd'](hook_input)

        # 記録側のpostToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(_utilities_directory, 'known-files-state', 'known-files-state.py'))
        state_path = state['get_state_path'](hook_input.get('sessionId'))

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答とツール実行を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, already_requested, command_texts = _get_turn_summary(transcript_path)
        # 同じ依頼で差し戻すのは1回だけにし、確認できない場合に停止できなくなるのを防ぐ。
        if last_assistant_msg is None or already_requested:
            return 0
        references = _get_speculative_references(last_assistant_msg, speculation_patterns)
        if not references:
            return 0

        # gitがない、またはリポジトリ外の場合は、cwd基準でだけ解決する。
        repository_root = _git['get_repository_root'](cwd, _GIT_TIMEOUT_SECONDS)
        seen_keys = _get_seen_keys(state, state_path)
        unread = []
        for candidate in references:
            full_path = _resolve_file(candidate, cwd, repository_root)
            if full_path is None:
                continue
            if state['get_file_key'](state['normalize_path'](cwd, full_path)) in seen_keys:
                continue
            if _is_read_by_command(full_path, candidate, cwd, command_texts):
                continue
            unread.append(candidate)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to inspect unread speculation: {ex}', file=sys.stderr)
        return 2

    if unread:
        files = ', '.join(unread[:_MAX_REPORTED_FILES])
        if len(unread) > _MAX_REPORTED_FILES:
            files += f' and {len(unread) - _MAX_REPORTED_FILES} more'
        reason = (
            f'{_REASON_PREFIX} that were not read in this session: {files}. '
            'Do not describe file contents from memory or assumptions. View each file and correct the statements '
            'based on its actual content, or state explicitly that the file was not checked.'
        )
        _hook_output['write_block_decision'](reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
