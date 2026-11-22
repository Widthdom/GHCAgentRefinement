import io
import os
import sys
from datetime import datetime, timezone
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'get_state_path'),
    os.path.join(_utilities_directory, 'write_json_atomically'),
]
import read_hook_input as _hook_input
from get_state_path import get_state_path as _get_state_path
from write_json_atomically import write_json_atomically as _write_json_atomically


def main():
    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # userPromptSubmittedのタイミングでCopilotがPythonプロセスを起動し、
    # 標準入力パイプにイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    # 状態ファイルの保存先は、状態を読むagentStop hookと共有する。
    try:
        hook_input = _hook_input.read_hook_input()
        # getはキーが無くてもNoneを返す。
        prompt = hook_input.get('prompt')
        if not isinstance(prompt, str):
            raise ValueError('prompt must be a string.')
        state_path = _get_state_path('adversarial_review', hook_input.get('sessionId'), '.json')
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to parse userPromptSubmitted hook input: {ex}', file=sys.stderr)
        return 2

    # agentIdが存在する場合は、サブエージェントのコンテキストで動作していることを示すので、
    # 状態の上書きを避けるために終了する。
    if hook_input.get('agentId'):
        return 0

    # プロンプト末尾が厳密に ;; であれば有効にし、レビュー依頼を未処理にする。
    enabled = prompt.endswith(';;')

    # 状態ファイルに保存する内容をdictとして作成する。
    # 内部的な記録なのでUTCを使用する。
    # isoformat() で日時オブジェクトをISO 8601形式の文字列に変換する。
    # pending：レビュー待ちならTrueを設定する。
    state = {
        'updatedAt': datetime.now(timezone.utc).isoformat(),
        'enabled': enabled,
        'pending': enabled,
    }

    try:
        # 一時ファイルに書いてから置き換え、読む側のhookに書き込み途中の状態ファイルを読ませない。
        _write_json_atomically(state_path, state)
    except OSError as ex:
        print(f'Failed to save adversarial review mode: {ex}', file=sys.stderr)
        return 2

    return 0


if __name__ == '__main__':
    sys.exit(main())
