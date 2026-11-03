import argparse
import io
import json
import os
import re
import runpy
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast


# 定義ファイルのキー。speculationPatternsは推測とみなす表現の正規表現。
SPECULATION_PATTERNS_KEY = 'speculationPatterns'
# 引数にパスが含まれていれば、そのファイルを読んだとみなすツール。`cat`・`sed -n`・`rg <pattern> <file>` などでの閲覧はrecord-seen-filesが記録しないため。
COMMAND_READ_TOOL_NAMES = {'bash', 'powershell', 'rg', 'grep'}
FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
INLINE_CODE_PATTERN = re.compile(r'`([^`\n]+)`')
MARKDOWN_LINK_PATTERN = re.compile(r'\]\(([^()\s]+)\)')
# コードスパン外のパス。日本語の文中に続けて書かれても切り出せるよう、ASCIIの文字だけで構成し、区切りと拡張子を必須にする。
BARE_PATH_PATTERN = re.compile(r'(?<![A-Za-z0-9_./\\~-])((?:\.{1,2}/|/|~/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9]+)')
# 文の区切り。句点・感嘆符・疑問符の後と、改行、英文のピリオドに続く空白で区切る。
SENTENCE_BOUNDARY_PATTERN = re.compile(r'(?<=[。！？])|\n|(?<=[.!?])\s+')
# 末尾の `:12`・`:12-34`・`#L12`・`#L12-L34` は行番号として除く。
LINE_SUFFIX_PATTERN = re.compile(r'(?::\d+(?:-\d+)?|#L\d+(?:-L?\d+)?)$')
# プレースホルダ・glob・コマンドなど、実在するファイルを指さない表記に含まれる文字。
NON_PATH_CHARACTERS = re.compile(r'[\s<>{}*?\[\]|$%=@,;"\'()!^&+]')
EXTENSION_PATTERN = re.compile(r'\.[A-Za-z0-9]+$')
MAX_REPORTED_FILES = 10
REASON_PREFIX = 'The final response speculates about files'


def is_non_empty_string_list(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, str) and bool(item.strip()) for item in value))


def load_definitions(definitions_path):
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
    if not is_non_empty_string_list(definitions.get(SPECULATION_PATTERNS_KEY)):
        raise ValueError(f'Definitions require a non-empty array of non-empty strings for {SPECULATION_PATTERNS_KEY}.')
    try:
        return tuple(re.compile(pattern) for pattern in definitions[SPECULATION_PATTERNS_KEY])
    except re.PatternError as ex:
        raise ValueError(f'Invalid regular expression in {SPECULATION_PATTERNS_KEY}: {ex}') from ex


def get_turn_summary(transcript_path, state):
    # ユーザーの最新の依頼以降の最終応答と本hookによる差し戻し済みか、セッション全体でコマンド・検索ツールに渡した文字列を集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    last_assistant_msg = None
    already_requested = False
    command_texts = []
    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects' / 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        event_type = event.get('type')
        content = data.get('content')

        if event_type == 'user.message':
            if data.get('delivery') != 'queued':
                last_assistant_msg = None
                already_requested = False
            elif isinstance(content, str) and content.startswith(REASON_PREFIX):
                already_requested = True
        elif event_type == 'assistant.message':
            if isinstance(content, str) and content.strip():
                last_assistant_msg = content
        elif event_type == 'tool.execution_start':
            tool_name = data.get('toolName')
            if isinstance(tool_name, str) and tool_name.removeprefix('functions.') in COMMAND_READ_TOOL_NAMES:
                command_texts.extend(state['collect_strings'](data.get('arguments')))
    return last_assistant_msg, already_requested, command_texts


def get_sentences(response):
    # コードブロック内の出力例やコマンドは対象外とする。
    text = FENCED_CODE_PATTERN.sub('', response)
    return [sentence for sentence in SENTENCE_BOUNDARY_PATTERN.split(text) if sentence and sentence.strip()]


def get_candidates(sentence):
    # コードスパン・Markdownリンクの参照先・地の文のパスを、書かれた表記のまま返す。
    candidates = INLINE_CODE_PATTERN.findall(sentence) + MARKDOWN_LINK_PATTERN.findall(sentence)
    candidates += [
        re.sub(r'\.+$', '', match) for match in BARE_PATH_PATTERN.findall(INLINE_CODE_PATTERN.sub(' ', sentence))
    ]
    references = []
    for candidate in candidates:
        candidate = LINE_SUFFIX_PATTERN.sub('', candidate.strip())
        if '://' in candidate or NON_PATH_CHARACTERS.search(candidate):
            continue
        # 区切りも拡張子もない表記（`origin` など）はファイルを指さないとみなす。
        if '/' not in candidate and '\\' not in candidate and not EXTENSION_PATTERN.search(candidate):
            continue
        references.append(candidate)
    return references


def get_speculative_references(response, speculation_patterns):
    # 推測の表現と同じ文に書かれたパスを、出現順に重複なく返す。推測の判定にはコードスパンの中身を使わない。
    references = {}
    for sentence in get_sentences(response):
        prose = INLINE_CODE_PATTERN.sub(' ', sentence)
        if any(pattern.search(prose) for pattern in speculation_patterns):
            for candidate in get_candidates(sentence):
                references.setdefault(candidate, None)
    return list(references)


def get_repository_root(cwd):
    # gitがない、またはリポジトリ外の場合はNoneを返し、cwd基準でだけ解決する。
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    root = result.stdout.decode('utf-8', errors='replace').strip()
    return root if result.returncode == 0 and root else None


def resolve_file(candidate, cwd, repository_root):
    # 応答ではリポジトリルート基準のパスも使うため、cwdとリポジトリルートの両方を基準に解決する。
    # 実在しないパスは対象外とする（誤参照はdetect-phantom-referencesが扱う）。
    expanded = os.path.expanduser(candidate)
    for base in dict.fromkeys(filter(None, (cwd, repository_root))):
        full_path = os.path.join(base, expanded)
        if os.path.isfile(full_path):
            return full_path
    return None


def get_seen_keys(state, state_path):
    # record-seen-filesが閲覧・編集を記録したファイルのキー。状態がなければ何も読んでいない。
    if not os.path.isfile(state_path):
        return set()
    with state['open_state'](state_path) as connection:
        return {row[0] for row in connection.execute('SELECT file FROM files WHERE state = ?', (state['SEEN'],))}


def is_read_by_command(full_path, candidate, cwd, command_texts):
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
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        dest='defs_path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'unread-speculation-definitions.json'),
    )
    parsed_args = parser.parse_args()

    try:
        speculation_patterns = load_definitions(parsed_args.defs_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to load unread speculation definitions: {ex}', file=sys.stderr)
        return 2

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            print('agentStop hook input is empty.', file=sys.stderr)
            return 2
        hook_input = json.loads(hook_input_json)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f'Failed to parse agentStop hook input: {ex}', file=sys.stderr)
        return 2

    if not isinstance(hook_input, dict):
        print('agentStop hook input must be a JSON object.', file=sys.stderr)
        return 2

    # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
    if hook_input.get('agentId'):
        return 0

    transcript_path = hook_input.get('transcriptPath')
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        print('agentStop hook transcriptPath must be a non-empty string.', file=sys.stderr)
        return 2

    if not os.path.isfile(transcript_path):
        print(f'Transcript path is not a file or could not be verified as a file: {transcript_path}', file=sys.stderr)
        return 2

    cwd = hook_input.get('cwd')
    if not isinstance(cwd, str) or not cwd.strip() or not os.path.isabs(cwd):
        print('agentStop hook cwd must be a non-empty absolute path.', file=sys.stderr)
        return 2

    try:
        # 記録側のpostToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(str(Path(__file__).resolve().parents[3] / 'utilities' / 'known-files-state' / 'known-files-state.py'))
        state_path = state['get_state_path'](hook_input.get('sessionId'))

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答とツール実行を書き込み済みにする。
        runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event' / 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, already_requested, command_texts = get_turn_summary(transcript_path, state)
        # 同じ依頼で差し戻すのは1回だけにし、確認できない場合に停止できなくなるのを防ぐ。
        if last_assistant_msg is None or already_requested:
            return 0
        references = get_speculative_references(last_assistant_msg, speculation_patterns)
        if not references:
            return 0

        repository_root = get_repository_root(cwd)
        seen_keys = get_seen_keys(state, state_path)
        unread = []
        for candidate in references:
            full_path = resolve_file(candidate, cwd, repository_root)
            if full_path is None:
                continue
            if state['get_file_key'](state['normalize_path'](cwd, full_path)) in seen_keys:
                continue
            if is_read_by_command(full_path, candidate, cwd, command_texts):
                continue
            unread.append(candidate)
    except (OSError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to inspect unread speculation: {ex}', file=sys.stderr)
        return 2

    if unread:
        files = ', '.join(unread[:MAX_REPORTED_FILES])
        if len(unread) > MAX_REPORTED_FILES:
            files += f' and {len(unread) - MAX_REPORTED_FILES} more'
        reason = (
            f'{REASON_PREFIX} that were not read in this session: {files}. '
            'Do not describe file contents from memory or assumptions. View each file and correct the statements '
            'based on its actual content, or state explicitly that the file was not checked.'
        )
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
