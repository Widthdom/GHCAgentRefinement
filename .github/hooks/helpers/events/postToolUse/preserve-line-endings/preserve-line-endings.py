import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import closing
from typing import cast


# SDKがファイル書き込みとして扱う組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$', re.MULTILINE)
_UTF8_BOM = b'\xef\xbb\xbf'
_GIT_TIMEOUT_SECONDS = 5
_FORMAT_CONTEXT = (
    'The following edited files no longer match the file format of their committed version: {changes}. '
    'Editing tools can silently rewrite line endings, the byte order mark, or the final newline when '
    'a whole file is rewritten, which turns a small edit into a whole-file diff. Unless the user asked '
    'for this format change, restore the original format while keeping your intended content changes, '
    'for example by rewriting the file with the original line endings, and verify the result with git diff.'
)


def _get_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def _collect_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _collect_strings(item)


def _get_edited_paths(tool_name, tool_args, cwd):
    if tool_name == 'apply_patch':
        # パッチ本文の `*** Update File: <path>` などの行から対象を集める。削除・移動元は後で実在確認により除外する。
        paths = [match for text in _collect_strings(tool_args) for match in _PATCH_FILE_PATTERN.findall(text)]
    else:
        path = tool_args.get('path') if isinstance(tool_args, dict) else None
        paths = [path] if isinstance(path, str) and path.strip() else []
    # 相対パスはcwd基準で解決し、同じファイルを別表記でも同一と判定する。
    return sorted({os.path.normcase(os.path.realpath(os.path.join(cwd, path))) for path in paths})


def _get_committed_content(path):
    # HEADのファイルを、チェックアウト時と同じ変換（core.autocrlfや.gitattributesのeol）を通して取得する。
    # 変換前の内容と比べると、WindowsのautocrlfでCRLFになった作業ツリーを誤って変更扱いしてしまう。
    # gitがない、リポジトリ外、HEADにないファイル（新規・管理外）は比較できないためNoneを返す。
    try:
        result = subprocess.run(
            ['git', 'cat-file', '--filters', f'HEAD:./{os.path.basename(path)}'],
            cwd=os.path.dirname(path), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _get_line_ending_style(content):
    crlf = content.count(b'\r\n')
    lf = content.count(b'\n') - crlf
    if crlf and lf:
        return 'mixed CRLF/LF'
    if crlf:
        return 'CRLF'
    if lf:
        return 'LF'
    return None


def _ends_with_newline(content):
    return content.endswith(b'\n')


def _get_format_changes(committed, current):
    # NUL文字を含むファイルはバイナリまたはUTF-16などとみなし、改行の判定をしない。
    if b'\0' in committed or b'\0' in current:
        return []
    changes = []
    committed_style = _get_line_ending_style(committed)
    current_style = _get_line_ending_style(current)
    # 元から混在しているファイルと、改行が1つもない内容は統一の基準がないため比べない。
    if committed_style in ('CRLF', 'LF') and current_style and current_style != committed_style:
        changes.append(f'line endings changed from {committed_style} to {current_style}')
    if committed.startswith(_UTF8_BOM) != current.startswith(_UTF8_BOM):
        changes.append('UTF-8 BOM removed' if committed.startswith(_UTF8_BOM) else 'UTF-8 BOM added')
    # 空ファイルには末尾の改行という概念がないため、どちらかが空なら比べない。
    if committed.removeprefix(_UTF8_BOM) and current.removeprefix(_UTF8_BOM):
        if _ends_with_newline(committed) != _ends_with_newline(current):
            changes.append('final newline removed' if _ends_with_newline(committed) else 'final newline added')
    return changes


def _get_display_path(path, cwd):
    # cwd配下のファイルはOSによらず `/` 区切りの相対パスで示し、助言を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd).replace(os.sep, '/')
    except ValueError:
        pass
    return path


def _record_notifications(state_path, results):
    # results：(パス, パスのSHA-256, 変更点のリスト) のリスト。戻り値は、今回初めて通知する (パス, 変更点) のリスト。
    new_notifications = []
    # SQLiteのトランザクションで、並列に編集しても同じ変更を重複して通知しない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS notifications ('
            'file TEXT NOT NULL, '
            'change TEXT NOT NULL, '
            'PRIMARY KEY (file, change))'
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for path, file_key, changes in results:
                # 解消した変更は記録を消し、再び崩れたときに改めて通知する。
                stored = connection.execute('SELECT change FROM notifications WHERE file = ?', (file_key,)).fetchall()
                for (change,) in stored:
                    if change not in changes:
                        connection.execute(
                            'DELETE FROM notifications WHERE file = ? AND change = ?', (file_key, change),
                        )
                unnotified = [
                    change for change in changes
                    if connection.execute(
                        'INSERT OR IGNORE INTO notifications (file, change) VALUES (?, ?)', (file_key, change),
                    ).rowcount
                ]
                if unnotified:
                    new_notifications.append((path, changes))
    return new_notifications


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

        session_id = hook_input.get('sessionId')
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('sessionId must be a non-empty UUID string.')
        try:
            parsed_session_id = uuid.UUID(session_id.strip())
        except ValueError:
            raise ValueError('sessionId must be a valid UUID.') from None

        for field in ('cwd', 'toolName'):
            value = hook_input.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{field} must be a non-empty string.')
        if not os.path.isabs(hook_input['cwd']):
            raise ValueError('cwd must be an absolute path.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        tool_name = hook_input['toolName'].removeprefix('functions.')
        if tool_name not in _EDIT_TOOL_NAMES:
            return 0
        paths = _get_edited_paths(tool_name, _get_tool_args(hook_input['toolArgs']), hook_input['cwd'])

        results = []
        for path in paths:
            # 削除されたファイルと、パッチの移動元は比較対象がない。
            if not os.path.isfile(path):
                continue
            committed = _get_committed_content(path)
            if committed is None:
                continue
            with open(path, 'rb') as file:
                current = file.read()
            # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
            file_key = hashlib.sha256(path.encode('utf-8')).hexdigest()
            results.append((path, file_key, _get_format_changes(committed, current)))
        # hookは呼び出しごとに別プロセスなので、通知済みの変更をセッションUUID別に保存する。
        # DBファイル自体は自動削除しない。
        state_directory = os.path.join(
            tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'preserve-line-endings',
        )
        state_path = os.path.join(state_directory, f'{parsed_session_id}.sqlite3')
        notifications = []
        # 崩れたファイルがあるか、解消した記録を消す必要がある場合だけ状態を開く。
        # 形式が崩れない通常の編集では状態ファイルを作らない。
        if any(changes for _, _, changes in results) or (results and os.path.isfile(state_path)):
            os.makedirs(state_directory, exist_ok=True)
            notifications = _record_notifications(state_path, results)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check line endings: {ex}', file=sys.stderr)
        return 2

    if notifications:
        cwd = os.path.normcase(os.path.realpath(hook_input['cwd']))
        changes = '; '.join(
            f'{_get_display_path(path, cwd)} ({", ".join(file_changes)})' for path, file_changes in notifications
        )
        # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({'additionalContext': _FORMAT_CONTEXT.format(changes=changes)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
