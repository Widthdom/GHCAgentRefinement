import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from contextlib import closing
from typing import cast


FAILURE_THRESHOLD = 3
WINDOW_SECONDS = 300
RECOVERY_CONTEXT = (
    'The same tool has failed three times within five minutes with identical '
    'arguments and error text in the same working directory. Before trying again, briefly state '
    'the observed facts, your hypothesis about the cause, and what you will '
    'change in the next attempt. Unless you have evidence that the environment '
    'or input has changed, do not repeat the same call unchanged. Inspect related '
    'files or choose a different diagnostic approach. If you remain unable to '
    'resolve the failure, tell the user what you tried and what information is missing.'
)


def record_failure(state_path, fingerprint):
    # SQLiteのトランザクションで、並列に失敗しても回数の更新を取りこぼさない。
    # context managerだけでは接続は閉じないため、closingで終了時に閉じる。
    with closing(sqlite3.connect(state_path, timeout=3)) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS failures ('
            'fingerprint TEXT PRIMARY KEY, '
            'first_seen REAL NOT NULL, '
            'count INTEGER NOT NULL CHECK (count BETWEEN 1 AND 3))'
        )
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            # ロック待ちの前に時刻を取ると、並列処理の順序が前後して
            # 別のプロセスの記録を「未来」として消すため、取得後に時刻を取る。
            now = time.time()
            # 古い失敗と、時計が巻き戻った場合の未来の記録は引き継がない。
            connection.execute(
                'DELETE FROM failures WHERE first_seen < ? OR first_seen > ?',
                (now - WINDOW_SECONDS, now),
            )
            previous = connection.execute(
                'SELECT count FROM failures WHERE fingerprint = ?', (fingerprint,),
            ).fetchone()
            previous_count = previous[0] if previous else 0
            count = min(previous_count + 1, FAILURE_THRESHOLD)
            # 集計期間は最初の失敗が基準。更新時にもfirst_seenを延長しない。
            # 成功や別の失敗が間に入っても、この組み合わせの回数は維持する。
            connection.execute(
                'INSERT INTO failures (fingerprint, first_seen, count) VALUES (?, ?, ?) '
                'ON CONFLICT(fingerprint) DO UPDATE SET count = excluded.count',
                (fingerprint, now, count),
            )

    # 3回目だけ通知する。4回目以降は同じ助言を繰り返さない。
    return previous_count < FAILURE_THRESHOLD and count == FAILURE_THRESHOLD


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

        # サブエージェントの失敗を親の失敗として数えない。
        if hook_input.get('agentId'):
            return 0

        for field in ('cwd', 'toolName', 'error'):
            value = hook_input.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{field} must be a non-empty string.')
        if not os.path.isabs(hook_input['cwd']):
            raise ValueError('cwd must be an absolute path.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        # 引数中のJSONオブジェクトは、キーの記述順が違っても同一と判定する。
        # コマンド・引数・エラー本文は保存せず、比較用のSHA-256だけを保存する。
        identity = json.dumps(
            [os.path.normcase(os.path.realpath(hook_input['cwd'])),
             hook_input['toolName'], hook_input['toolArgs'], hook_input['error']],
            ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False,
        )
        fingerprint = hashlib.sha256(identity.encode('utf-8')).hexdigest()

        # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
        # 期限切れレコードは次回失敗時に削除する。DBファイル自体は自動削除しない。
        state_directory = os.path.join(
            tempfile.gettempdir(), 'ghc-agent-refinement', 'hook-states', 'repeated-failures',
        )
        os.makedirs(state_directory, exist_ok=True)
        state_path = os.path.join(state_directory, f'{parsed_session_id}.sqlite3')
        should_notify = record_failure(state_path, fingerprint)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to detect repeated tool failures: {ex}', file=sys.stderr)
        return 2

    if should_notify:
        # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
        # 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference
        # 終了コード2による助言も公式には記載があるが、ここでは
        # VS Code 1.143.1同梱SDKのhook processorで反映を確認した形式を使う。
        print(json.dumps({'additionalContext': RECOVERY_CONTEXT}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
