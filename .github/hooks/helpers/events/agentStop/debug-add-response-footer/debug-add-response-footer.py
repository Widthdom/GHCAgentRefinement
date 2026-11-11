import io
import json
import os
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
_parse_session_id = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['parse_session_id']
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))

_REASON_PREFIX = 'The final response footer is missing or incorrect.'
# 同じ依頼で差し戻す上限。フッターの時刻や機能したhookは差し戻しのたびに変わるため、合わせ損ねても止まれるようにする。
# 1回では時刻の分の繰り上がりなどで合わせ損ねた場合に直せないため、2回とする。
_MAX_CORRECTIONS = 2


def _count_corrections(transcript_path):
    # ユーザーの最新の依頼以降に、本hookが差し戻した回数を返す。
    return sum(
        1 for event_type, data in _turn_events['get_turns'](transcript_path)[-1]
        if event_type == 'user.message' and data.get('delivery') == 'queued'
        and isinstance(data.get('content'), str) and data['content'].startswith(_REASON_PREFIX)
    )


def _get_last_assistant_msg(transcript_path):
    # トランスクリプト全体から、空白以外を含む最後の応答の文を返す。なければNone。
    return _turn_events['get_last_assistant_message'](
        [event for events in _turn_events['get_turns'](transcript_path) for event in events]
    )


def _get_footer_correction(transcript_path, session_id, last_assistant_msg):
    # __file__：このスクリプト自身の絶対パスが設定されている組み込み変数。
    script_directory = os.path.dirname(os.path.abspath(__file__))

    current_jst = runpy.run_path(
        os.path.join(_utilities_directory, 'get-current-jst', 'get-current-jst.py')
    )['current_jst']

    used_hooks = runpy.run_path(os.path.join(script_directory, 'get-used-hooks.py'))['get_used_hooks'](
        transcript_path,
        # utilities → helpers → hooks の順に上がった .github/hooks ディレクトリ。
        os.path.dirname(os.path.dirname(_utilities_directory)),
    ) or ['none']

    # hookランナーが記録した、実際に機能したhook。同時に動くagentStop hookは含めない。
    fired_hooks = runpy.run_path(os.path.join(script_directory, 'get-fired-hooks.py'))['get_fired_hooks'](
        transcript_path,
        session_id,
    ) or ['none']

    used_skills = runpy.run_path(os.path.join(script_directory, 'get-used-skills.py'))['get_used_skills'](
        transcript_path
    ) or ['none']

    formatted_footer = runpy.run_path(os.path.join(script_directory, 'format-response-footer.py'))['format_response_footer'](
        used_hooks,
        fired_hooks,
        used_skills,
        current_jst,
    )

    is_footer_valid = runpy.run_path(os.path.join(script_directory, 'check-response-footer.py'))['test_response_footer'](
        last_assistant_msg,
        used_hooks,
        fired_hooks,
        used_skills,
        current_jst,
    )

    # フッターが有効であればNoneを返し、無効であれば整形済みフッターを返す。
    return None if is_footer_valid else formatted_footer


def main():
    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # agentStopのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    try:
        hook_input = _hook_input['read_hook_input']()
        transcript_path = _hook_input['get_transcript_path'](hook_input)
        parsed_session_id = _parse_session_id(hook_input.get('sessionId'))
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to parse agentStop hook input: {ex}', file=sys.stderr)
        return 2

    # サブエージェントの停止は対象外。フッターを付けるのは、ユーザーへ報告する親の最終応答のため。
    if hook_input.get('agentId'):
        return 0

    try:
        # C# のdelegateのように、外部スクリプトから関数を取得する。
        get_debug_mode = runpy.run_path(
            # __file__：このスクリプト自身の絶対パスが設定されている組み込み変数。
            os.path.join(_utilities_directory, 'get-debug-mode', 'get-debug-mode.py')
        )['get_debug_mode']
    except (OSError, SyntaxError, KeyError) as ex:
        # f'...' は formatted string literalのことでf-stringとも呼ばれる。文字列の中に {式} の値を埋め込める書き方。C#の $"..." に相当する。
        # !r は repr() を使って値を文字列化する指定。例外オブジェクトの型とメッセージを含めて表示する。
        #   print(f'{ex}')    # str(ex)：人が読むためのメッセージ
        #   print(f'{ex!r}')  # repr(ex)：デバッグ向けの表現
        print(f'Failed to load debug-mode helper: {ex!r}', file=sys.stderr)
        return 2

    # UnicodeDecodeErrorとJSONDecodeErrorはValueErrorの派生クラスなので、ValueErrorより先に捕捉する。
    try:
        # 前段で外部スクリプトから取得した関数を実行
        is_debug_mode = get_debug_mode(parsed_session_id)
    except OSError as ex:
        print(f'Failed to read debug-mode state file: {ex}', file=sys.stderr)
        return 2
    except UnicodeDecodeError as ex:
        print(f'Debug-mode state file is not valid UTF-8: {ex}', file=sys.stderr)
        return 2
    except json.JSONDecodeError as ex:
        print(f'Debug-mode state file is not valid JSON: {ex}', file=sys.stderr)
        return 2
    except ValueError as ex:
        print(f'Debug-mode state is invalid: {ex}', file=sys.stderr)
        return 2

    if not is_debug_mode:
        return 0

    try:
        # GitHub Copilot はトランスクリプトに非同期で追記するため、agentStop hookが起動した直後は、最終応答がまだ書き込まれていない可能性がある。
        # 自身のhook.startが書き込まれるまで最大5秒待ち、それより前に発生した最終応答を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg = _get_last_assistant_msg(transcript_path)
        if last_assistant_msg is None:
            print('No assistant response was found in the transcript.', file=sys.stderr)
            return 2

        if _count_corrections(transcript_path) >= _MAX_CORRECTIONS:
            return 0

        footer_correction = _get_footer_correction(transcript_path, str(parsed_session_id), last_assistant_msg)
    except (OSError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to inspect response footer: {ex}', file=sys.stderr)
        return 2

    if footer_correction is not None:
        # ensure_ascii=False：日本語などを \uXXXX にせず、そのまま出力する。出力先はUTF-8に設定するため問題なし。
        # separators=(',', ':')：項目間やキーと値の間に余分な空白を入れない。（出力をコンパクトにするための指定で、省略してもJSONとしては正しく伝わる。）
        # 引用符・バックスラッシュ・改行など、JSONとして必要なエスケープは ensure_ascii=False で行われる
        print(json.dumps({
            'decision': 'block',
            'reason': (
                f'{_REASON_PREFIX} Preserve the response body. '
                'Replace any existing footer blocks with the exact block below; if no footer exists, '
                'append it to the end. The corrected response must contain exactly one footer block:\n'
                '\n'
                f'{footer_correction}'
            ),
        }, ensure_ascii=False, separators=(',', ':')))
    return 0


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
