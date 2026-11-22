import io
import json
import os
import sqlite3
import sys
import time
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'write_hook_output'),
    os.path.join(_utilities_directory, 'get_sha256_digest'),
    os.path.join(_utilities_directory, 'get_state_path'),
]
import read_hook_input as _hook_input
import write_hook_output as _hook_output
from get_sha256_digest import get_text_sha256_digest as _get_text_sha256_digest
import get_state_path as _state_path_utility
_get_state_path = _state_path_utility.get_state_path


_FAILURE_THRESHOLD = 3
_WINDOW_SECONDS = 300
_RECOVERY_CONTEXT = (
    'The same tool has failed three times within five minutes with identical '
    'arguments and error text in the same working directory. Before trying again, briefly state '
    'the observed facts, your hypothesis about the cause, and what you will '
    'change in the next attempt. Unless you have evidence that the environment '
    'or input has changed, do not repeat the same call unchanged. Inspect related '
    'files or choose a different diagnostic approach. If you remain unable to '
    'resolve the failure, tell the user what you tried and what information is missing.'
)


def _record_failure(state_path, fingerprint):
    # SQLiteのトランザクションで、並列に失敗しても回数の更新を取りこぼさない。
    with _state_path_utility.open_state_database(
        state_path,
        'CREATE TABLE IF NOT EXISTS failures ('
        'fingerprint TEXT PRIMARY KEY, '
        'first_seen REAL NOT NULL, '
        'count INTEGER NOT NULL CHECK (count BETWEEN 1 AND 3))',
    ) as connection:
        # ロック待ちの前に時刻を取ると、並列処理の順序が前後して
        # 別のプロセスの記録を「未来」として消すため、取得後に時刻を取る。
        now = time.time()
        # 古い失敗と、時計が巻き戻った場合の未来の記録は引き継がない。
        connection.execute(
            'DELETE FROM failures WHERE first_seen < ? OR first_seen > ?',
            (now - _WINDOW_SECONDS, now),
        )
        previous = connection.execute(
            'SELECT count FROM failures WHERE fingerprint = ?', (fingerprint,),
        ).fetchone()
        previous_count = previous[0] if previous else 0
        count = min(previous_count + 1, _FAILURE_THRESHOLD)
        # 集計期間は最初の失敗が基準。更新時にもfirst_seenを延長しない。
        # 成功や別の失敗が間に入っても、この組み合わせの回数は維持する。
        connection.execute(
            'INSERT INTO failures (fingerprint, first_seen, count) VALUES (?, ?, ?) '
            'ON CONFLICT(fingerprint) DO UPDATE SET count = excluded.count',
            (fingerprint, now, count),
        )

    # 3回目だけ通知する。4回目以降は同じ助言を繰り返さない。
    return previous_count < _FAILURE_THRESHOLD and count == _FAILURE_THRESHOLD


def main():
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input.read_hook_input()

        state_path = _get_state_path('repeated-failures', hook_input.get('sessionId'))

        # サブエージェントの失敗を親の失敗として数えない。
        if hook_input.get('agentId'):
            return 0

        cwd = _hook_input.get_cwd(hook_input)
        tool_name, tool_args = _hook_input.get_tool_call(hook_input)
        error = hook_input.get('error')
        if not isinstance(error, str) or not error.strip():
            raise ValueError('error must be a non-blank string.')

        # 引数中のJSONオブジェクトは、キーの記述順が違っても同一と判定する。
        # コマンド・引数・エラー本文は保存せず、比較用のSHA-256だけを保存する。
        identity = json.dumps(
            [os.path.normcase(os.path.realpath(cwd)),
             tool_name, tool_args, error],
            ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False,
        )
        fingerprint = _get_text_sha256_digest(identity)

        # hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。
        # 期限切れレコードは次回失敗時に削除する。DBファイル自体は自動削除しない。
        should_notify = _record_failure(state_path, fingerprint)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to detect repeated tool failures: {ex}', file=sys.stderr)
        return 2

    if should_notify:
        # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
        # 終了コード2による助言も公式には記載があるが、ここでは
        # VS Code 1.143.1同梱SDKのhook processorで反映を確認した形式を使う。
        _hook_output.write_additional_context(_RECOVERY_CONTEXT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
