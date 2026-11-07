import argparse
import io
import json
import os
import re
import runpy
import sqlite3
import sys
import tempfile
import uuid
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_filter_first_time_denials = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-first-time-denials', 'filter-first-time-denials.py',
))['filter_first_time_denials']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']


_SHELL_TOOL_NAMES = {'bash', 'powershell'}
_UPDATE_COMMANDS_KEY = 'updateCommands'
# コマンドを `;`・`&&`・`||`・`|`・改行で区切った単位ごとに照合し、`git push -u; npm test` などを結び付けない。
# `2>&1` の `&` では区切らない。
_SEGMENT_SEPARATOR_PATTERN = re.compile(r'&&|\|\||[;|\n]')
_DENY_REASON = (
    'Shell command denied: it would accept new snapshots or golden files in bulk ({kinds}). '
    'Accepting the current output rewrites every expected result to whatever the code produces now, '
    'so a regression becomes the new expectation and the tests pass without fixing anything. '
    'First run the tests without the update option and inspect each snapshot diff. '
    'Fix the implementation if the diff is not the intended behavior. '
    'If every change is intended, for example because the user asked for the new output, run the same command '
    'again and report which snapshots changed and why; this check denies each kind of update only once per session.'
)


def _load_update_commands(path):
    # 戻り値は (名前, コンパイル済みの正規表現) のリスト。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    entries = definitions.get(_UPDATE_COMMANDS_KEY) if isinstance(definitions, dict) else None
    if not isinstance(entries, list) or not entries or not all(
        isinstance(entry, dict) and isinstance(entry.get('name'), str) and entry['name'].strip()
        and isinstance(entry.get('pattern'), str) and entry['pattern'].strip()
        for entry in entries
    ):
        raise ValueError(f'snapshot update definitions require a non-empty array of {{name, pattern}} for {_UPDATE_COMMANDS_KEY}.')
    try:
        return [(entry['name'], re.compile(entry['pattern'])) for entry in entries]
    except re.PatternError as ex:
        raise ValueError(f'invalid regular expression in {_UPDATE_COMMANDS_KEY}: {ex}') from None


def _get_update_kinds(update_commands, command):
    # 戻り値は、コマンドに含まれる一括更新の種類の名前（定義順・重複なし）。
    segments = _SEGMENT_SEPARATOR_PATTERN.split(command)
    return [
        name for name, pattern in update_commands
        if any(pattern.search(segment) for segment in segments)
    ]


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'snapshot-update-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        update_commands = _load_update_commands(parsed_arguments.definitions_path)

        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        session_id = hook_input.get('sessionId')
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('sessionId must be a non-empty UUID string.')
        try:
            parsed_session_id = uuid.UUID(session_id.strip())
        except ValueError:
            raise ValueError('sessionId must be a valid UUID.') from None
        # preToolUseには、呼び出すツール1件の名前（toolName）と引数（toolArgs）が渡される。
        tool_name = hook_input.get('toolName')
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError('toolName must be a non-empty string.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        tool_args = _unwrap_tool_args(hook_input['toolArgs']) if tool_name.removeprefix('functions.') in _SHELL_TOOL_NAMES else None
        command = tool_args.get('command') if isinstance(tool_args, dict) else None
        kinds = _get_update_kinds(update_commands, command) if isinstance(command, str) else []

        # 一括更新がなければ、状態ファイルを作らずに許可する。
        denied_kinds = []
        if kinds:
            # hookは呼び出しごとに別プロセスなので、拒否済みの種類をセッションUUID別に保存する。
            # DBファイル自体は自動削除しない。
            state_path = os.path.join(
                tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'block-snapshot-rubber-stamp',
                f'{parsed_session_id}.sqlite3',
            )
            # 同じ種類の拒否はセッションで1回だけにし、意図した更新なら再実行で通す。
            denied_kinds = _filter_first_time_denials(state_path, [(kind, kind) for kind in kinds])
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check snapshot updates: {ex}', file=sys.stderr)
        return 2

    if denied_kinds:
        reason = _DENY_REASON.format(kinds='; '.join(denied_kinds))
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
