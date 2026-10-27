import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import uuid
from contextlib import closing


# SDKがファイル書き込みとして扱う組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$', re.MULTILINE)
WINDOW_SECONDS = 600
# 削除済み・ファイル以外を表す内容ハッシュ。SHA-256の16進表記とは衝突しない。
MISSING_CONTENT = 'missing'
OSCILLATION_CONTEXT = (
    'The following files have just returned to content they already had earlier '
    'within the last ten minutes, after being changed in between: {files}. '
    'You may be oscillating between alternative edits instead of making progress. '
    'Before editing these files again, briefly state which versions you have tried, '
    'why each was rejected, and what evidence will decide between them. '
    'Do not switch back and forth again without new evidence. If the user explicitly '
    'asked for this revert, continue as requested. If you cannot decide, ask the user.'
)


def get_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列や生のパッチ文字列で渡される場合もある。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def collect_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item)


def get_edited_paths(tool_name, tool_args, cwd):
    if tool_name == 'apply_patch':
        # パッチ本文の `*** Update File: <path>` などの行から対象を集める。移動元も削除扱いで含める。
        paths = [match for text in collect_strings(tool_args) for match in PATCH_FILE_PATTERN.findall(text)]
    else:
        path = tool_args.get('path') if isinstance(tool_args, dict) else None
        paths = [path] if isinstance(path, str) and path.strip() else []
    # 相対パスはcwd基準で解決し、同じファイルを別表記でも同一と判定する。
    return sorted({os.path.normcase(os.path.realpath(os.path.join(cwd, path))) for path in paths})


def get_content_hash(path):
    if not os.path.isfile(path):
        return MISSING_CONTENT
    with open(path, 'rb') as file:
        return hashlib.sha256(file.read()).hexdigest()


def get_display_path(path, cwd):
    # cwd配下のファイルは相対パスで示し、助言を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.commonpath([cwd, path]) == cwd:
            return os.path.relpath(path, cwd)
    except ValueError:
        pass
    return path


def record_edits(state_path, edits):
    # 戻り値は、変更を挟んで以前と同じ内容に戻ったファイルパスのリスト。
    oscillated_paths = []
    # SQLiteのトランザクションで、並列に編集しても履歴の更新を取りこぼさない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS contents ('
            'file TEXT NOT NULL, '
            'content TEXT NOT NULL, '
            'last_seen REAL NOT NULL, '
            'sequence INTEGER NOT NULL, '
            'notified INTEGER NOT NULL CHECK (notified IN (0, 1)), '
            'PRIMARY KEY (file, content))'
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            # ロック待ちの後に時刻を取り、並列処理の記録を「未来」として消さない。
            now = time.time()
            # 古い内容と、時計が巻き戻った場合の未来の記録は引き継がない。
            connection.execute(
                'DELETE FROM contents WHERE last_seen < ? OR last_seen > ?',
                (now - WINDOW_SECONDS, now),
            )
            # 内容の新旧はファイルをまたいだ連番で決める。同時刻の記録でも順序が崩れない。
            sequence = connection.execute('SELECT COALESCE(MAX(sequence), 0) + 1 FROM contents').fetchone()[0]
            for path, file_key, content in edits:
                latest = connection.execute(
                    'SELECT content FROM contents WHERE file = ? ORDER BY sequence DESC LIMIT 1', (file_key,),
                ).fetchone()
                # 内容が変わらない書き込みは往復ではないため、履歴を更新しない。
                if latest and latest[0] == content:
                    continue
                previous = connection.execute(
                    'SELECT notified FROM contents WHERE file = ? AND content = ?', (file_key, content),
                ).fetchone()
                # 通知時はそのファイルの既存の内容をすべて通知済みにし、以降の同じ往復で助言を繰り返さない。
                # 新しい内容を経由して再び往復した場合だけ、改めて通知する。
                notified = 1 if previous else 0
                if previous and not previous[0]:
                    oscillated_paths.append(path)
                    connection.execute('UPDATE contents SET notified = 1 WHERE file = ?', (file_key,))
                connection.execute(
                    'INSERT INTO contents (file, content, last_seen, sequence, notified) VALUES (?, ?, ?, ?, ?) '
                    'ON CONFLICT(file, content) DO UPDATE SET '
                    'last_seen = excluded.last_seen, sequence = excluded.sequence, notified = excluded.notified',
                    (file_key, content, now, sequence, notified),
                )
                sequence += 1
    return oscillated_paths


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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

        # サブエージェントの編集を親の編集として数えない。
        if hook_input.get('agentId'):
            return 0

        for field in ('cwd', 'toolName'):
            value = hook_input.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{field} must be a non-empty string.')
        if not os.path.isabs(hook_input['cwd']):
            raise ValueError('cwd must be an absolute path.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        tool_name = hook_input['toolName'].removeprefix('functions.')
        if tool_name not in EDIT_TOOL_NAMES:
            return 0
        paths = get_edited_paths(tool_name, get_tool_args(hook_input['toolArgs']), hook_input['cwd'])
        if not paths:
            return 0

        # 編集後のファイル内容を読む。パスと内容は保存せず、比較用のSHA-256だけを保存する。
        edits = [
            (path, hashlib.sha256(path.encode('utf-8')).hexdigest(), get_content_hash(path))
            for path in paths
        ]

        # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
        # 期限切れレコードは次回編集時に削除する。DBファイル自体は自動削除しない。
        state_directory = os.path.join(
            tempfile.gettempdir(), 'ghc-agent-refinement', 'edit-oscillation',
        )
        os.makedirs(state_directory, exist_ok=True)
        state_path = os.path.join(state_directory, f'{parsed_session_id}.sqlite3')
        oscillated_paths = record_edits(state_path, edits)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to detect edit oscillation: {ex}', file=sys.stderr)
        return 2

    if oscillated_paths:
        cwd = os.path.normcase(os.path.realpath(hook_input['cwd']))
        files = ', '.join(get_display_path(path, cwd) for path in oscillated_paths)
        # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        print(json.dumps({'additionalContext': OSCILLATION_CONTEXT.format(files=files)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
