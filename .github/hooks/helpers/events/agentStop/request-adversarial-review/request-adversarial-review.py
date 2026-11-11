import io
import json
import os
import runpy
import subprocess
import sys
from datetime import datetime, timezone
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_write_json_atomically = runpy.run_path(os.path.join(
    _utilities_directory, 'write-json-atomically', 'write-json-atomically.py',
))['write_json_atomically']


_REVIEW_REASON = (
    'The user requested adversarial review with the ;; suffix, and git status '
    'reports working-tree changes. Before finishing, invoke the custom subagent '
    '"adversarial-review" defined in .github/agents/adversarial-review.agent.md. '
    'Pass the user requirements and repository working directory. Review all '
    'staged, unstaged, and untracked changes, including pre-existing changes. '
    'Wait for the subagent result and report it to the user. Do not modify files, '
    'automatically fix findings, or request another review for this prompt. '
    'If delegation is unavailable or fails, explicitly report that the review '
    'was not completed; do not claim success or silently substitute self-review.'
)


def main():
    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # agentStopのタイミングでCopilotがPythonプロセスを起動し、
    # 標準入力パイプにイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    # 状態ファイルの保存先は、状態を設定するuserPromptSubmitted hookと共有する。
    try:
        hook_input = _hook_input['read_hook_input']()
        state_path = _get_state_path('adversarial-review', hook_input.get('sessionId'), '.json')
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to parse agentStop hook input: {ex}', file=sys.stderr)
        return 2

    # サブエージェントの停止では親セッションの状態を消費しない。
    if hook_input.get('agentId'):
        return 0

    # 'r'：読み込みモード。
    try:
        with open(state_path, 'r', encoding='utf-8') as state_file:
            state_json = state_file.read()
    except FileNotFoundError:
        return 0
    except OSError as ex:
        print(f'Failed to read adversarial review state: {ex}', file=sys.stderr)
        return 2

    try:
        state = json.loads(state_json)
    except (UnicodeError, json.JSONDecodeError) as ex:
        print(f'Failed to parse adversarial review state: {ex}', file=sys.stderr)
        return 2

    # 変換結果がJSONオブジェクト（最外側が{}）でなければ異常終了。
    if not isinstance(state, dict):
        print('Adversarial review state must be a JSON object.', file=sys.stderr)
        return 2

    # 状態の値がbool型でなければ異常終了。
    # boolはintのサブクラスなので、型がboolかを直接確認する。
    enabled = state.get('enabled')
    if type(enabled) is not bool:
        print('Adversarial review state enabled must be a boolean.', file=sys.stderr)
        return 2

    pending = state.get('pending')
    if type(pending) is not bool:
        print('Adversarial review state pending must be a boolean.', file=sys.stderr)
        return 2

    updated_at = state.get('updatedAt')
    if not isinstance(updated_at, str):
        print('Adversarial review state updatedAt must be a string.', file=sys.stderr)
        return 2

    # 矛盾する組み合わせのときは異常終了。
    if pending and not enabled:
        print('Adversarial review state cannot be pending while disabled.', file=sys.stderr)
        return 2

    # 敵対的レビューモードが無効なら終了。
    if not enabled:
        return 0

    # レビュー待ち状態でなければ終了。
    if not pending:
        return 0

    # cwd：Current Working Directory。絶対パスで指定される。
    try:
        cwd = _hook_input['get_cwd'](hook_input)
    except ValueError as ex:
        print(f'Failed to parse agentStop hook input: {ex}', file=sys.stderr)
        return 2

    # シェルを介さずにgit statusを実行する。
    #   cwdやファイル名に空白・記号が含まれてもシェル用の引用処理が不要で、入力がシェルコマンドとして解釈される危険も避けられる。
    # -z：ファイル名に空白や改行が含まれていても正しく判定できるようにする。
    # --untracked-files=all：未追跡の新規ディレクトリをディレクトリ単位でまとめず、配下の新規ファイルを個別に列挙する。
    try:
        status = subprocess.run(
            ['git', 'status', '--porcelain=v1', '--untracked-files=all', '-z'],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired as ex:
        print(f'git status timed out: {ex}', file=sys.stderr)
        return 2
    except OSError as ex:
        print(f'Failed to run git status: {ex}', file=sys.stderr)
        return 2

    if status.returncode != 0:
        error_message = status.stderr.decode('utf-8', errors='replace').strip()
        print(
            f'git status failed with exit code {status.returncode}: {error_message}',
            file=sys.stderr,
        )
        return 2

    # stdoutはgit statusの結果。空であれば終了する。
    if not status.stdout:
        return 0

    # 状態ファイルに保存する内容をdictとして作成する。
    # 内部的な記録なのでUTCを使用する。
    # isoformat() で日時オブジェクトをISO 8601形式の文字列に変換する。
    # レビュー待ち状態を解除するため、pendingをFalseに設定する。
    state = {
        'updatedAt': datetime.now(timezone.utc).isoformat(),
        'enabled': True,
        'pending': False,
    }

    try:
        # 一時ファイルに書いてから置き換え、不完全な状態ファイルを読み込ませない。
        _write_json_atomically(state_path, state)
    except OSError as ex:
        print(f'Failed to save adversarial review state: {ex}', file=sys.stderr)
        return 2

    # この標準出力を受け取ったhook呼び出し元は、decision:blockに従って停止したのち、
    # reasonに従って敵対的レビューエージェントを呼び出すことになる。
    print(json.dumps({'decision': 'block', 'reason': _REVIEW_REASON}, ensure_ascii=False))

    return 0


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
