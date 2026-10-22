import json
import os
import runpy
import sys
import time
import uuid
from pathlib import Path


def _is_agent_stop_event_recorded(transcript_path, hook_timestamp):
    # トランスクリプトに、今回実行中のagentStop hookのhook.startイベントが書き込まれているかを返す。
    #   過去のターンのagentStopと取り違えないよう、hook入力のtimestampが一致するものだけを対象とする。

    # `utf-8-sig` を指定することで、BOMが存在する場合は取り除いて読むことができる。
    # with：処理を抜けるときの後片付けを自動で行う構文（C#のusingに相当）。
    with open(transcript_path, encoding='utf-8-sig') as file:
        # enumerate(file, 1)：1始まりの行番号を付けながら1行ずつ読む。
        for line_number, line in enumerate(file, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # 空行や非同期追記中の不完全な行は読み飛ばし、待機後の本処理で改めて検証する。
                continue

            # 変換結果がJSONオブジェクト（最外側が{}）でなければ異常とする。
            if not isinstance(event, dict):
                raise ValueError(f'Transcript event must be a JSON object at line {line_number}.')

            if event.get('type') != 'hook.start':
                continue

            # getはキーが無ければ第2引数の既定値を返す。
            data = event.get('data', {})
            if not isinstance(data, dict):
                raise ValueError(f'Event data must be a JSON object at line {line_number}.')

            if data.get('hookType') != 'agentStop':
                continue

            # hook入力の形はCopilot側の仕様に依存するため、想定外の形でも異常とはせず対象外とする。
            hook_input = data.get('input')
            if not isinstance(hook_input, dict):
                continue

            if hook_input.get('timestamp') == hook_timestamp:
                return True

    return False


def _get_last_assistant_msg(transcript_path):
    last_assistant_msg = None
    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        if event.get('type') != 'assistant.message':
            continue

        # getはキーが無ければ第2引数の既定値を返す。
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')

        # getはキーが無くてもNoneを返す。
        content = data.get('content')
        if not isinstance(content, str):
            continue

        if not content.strip():
            continue

        last_assistant_msg = content
    return last_assistant_msg


def _get_footer_correction(transcript_path, last_assistant_msg):
    # __file__：このスクリプト自身の絶対パスが設定されている組み込み変数。
    script_directory = Path(__file__).resolve().parent

    current_jst = runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'get-current-jst.py')
    )['current_jst']

    used_hooks = runpy.run_path(str(script_directory / 'get-used-hooks.py'))['get_used_hooks'](
        transcript_path,
        # parents[4]：このスクリプトから5階層上の .github/hooks ディレクトリ。
        str(Path(__file__).resolve().parents[4]),
    ) or ['none']

    used_skills = runpy.run_path(str(script_directory / 'get-used-skills.py'))['get_used_skills'](
        transcript_path
    ) or ['none']

    formatted_footer = runpy.run_path(str(script_directory / 'format-response-footer.py'))['format_response_footer'](
        used_hooks,
        used_skills,
        current_jst,
    )

    is_footer_valid = runpy.run_path(str(script_directory / 'check-response-footer.py'))['test_response_footer'](
        last_assistant_msg,
        used_hooks,
        used_skills,
        current_jst,
    )

    # フッターが有効であればNoneを返し、無効であれば整形済みフッターを返す。
    return None if is_footer_valid else formatted_footer


def main():
    # replace：変換できない文字があったら、例外を出さずに ? に置き換える。
    #   strict(既定)：UnicodeEncodeError を発生させる。
    #   ignore：その文字を捨てる。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    # agentStopのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    #   改行コードを変換せずに扱うため、バイト列として読み取ってからUTF-8として復号化する。
    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        # Pythonの not は、対象を真偽として評価してから反転する。
        # strip() が返すのは文字列だが、真偽として判定されれば空文字列はFalse、それ以外はTrueとなる。
        #   if not text:   # 文字列が空のとき
        #   if not items:  # リストなどが空のとき
        #   if not data:   # 辞書などが空のとき
        if not hook_input_json.strip():
            print('agentStop hook input is empty.', file=sys.stderr)
            return 2
        hook_input = json.loads(hook_input_json)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f'Failed to parse agentStop hook input: {ex}', file=sys.stderr)
        return 2

    # 変換結果がJSONオブジェクト（最外側が{}）でなければ異常終了。
    #   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
    if not isinstance(hook_input, dict):
        print('agentStop hook input must be a JSON object.', file=sys.stderr)
        return 2

    # getはキーが無くてもNoneを返す。
    transcript_path = hook_input.get('transcriptPath')
    if not isinstance(transcript_path, str):
        print('agentStop hook transcriptPath must be a string.', file=sys.stderr)
        return 2

    if not transcript_path.strip():
        print('agentStop hook transcriptPath must not be empty or whitespace.', file=sys.stderr)
        return 2

    if not os.path.isfile(transcript_path):
        print(f'Transcript path is not a file or could not be verified as a file: {transcript_path}', file=sys.stderr)
        return 2

    # getはキーが無くてもNoneを返す。
    session_id = hook_input.get('sessionId')

    if not isinstance(session_id, str):
        print('agentStop hook sessionId must be a string.', file=sys.stderr)
        return 2

    if not session_id.strip():
        print('agentStop hook sessionId must not be empty or whitespace.', file=sys.stderr)
        return 2

    try:
        parsed_session_id = uuid.UUID(session_id.strip())
    except ValueError:
        print('agentStop hook sessionId must be a valid UUID.', file=sys.stderr)
        return 2

    try:
        # C# のdelegateのように、外部スクリプトから関数を取得する。
        get_debug_mode = runpy.run_path(
            # __file__：このスクリプト自身の絶対パスが設定されている組み込み変数。
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'get-debug-mode.py')
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
        # イベントは発生順に追記されるので、自身のhook.startが追記されれば、それより前に発生した最終応答も書き込み済みとみなすことができる。
        hook_timestamp = hook_input.get('timestamp')
        # Pythonでは bool が int のサブクラスになっているので、その分を除外する必要がある。
        if isinstance(hook_timestamp, (int, float)) and not isinstance(hook_timestamp, bool):
            # monotonic：システムクロックに依存せず、単調増加する時刻を返す。タイムアウト判定に使用する。
            deadline = time.monotonic() + 5.0
            # _is_agent_stop_event_recorded() が True を返すまで、
            # 5秒の間0.05秒ごとにhook.startの書き込みを確認するループを開始する。
            while not _is_agent_stop_event_recorded(transcript_path, hook_timestamp):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)

        last_assistant_msg = _get_last_assistant_msg(transcript_path)
        if last_assistant_msg is None:
            print('No assistant response was found in the transcript.', file=sys.stderr)
            return 2

        footer_correction = _get_footer_correction(transcript_path, last_assistant_msg)
    except (OSError, ValueError) as ex:
        print(f'Failed to inspect response footer: {ex}', file=sys.stderr)
        return 2

    if footer_correction is not None:
        # ensure_ascii=False：日本語などを \uXXXX にせず、そのまま出力する。出力先はUTF-8に設定するため問題なし。
        # separators=(',', ':')：項目間やキーと値の間に余分な空白を入れない。（出力をコンパクトにするための指定で、省略してもJSONとしては正しく伝わる。）
        # 引用符・バックスラッシュ・改行など、JSONとして必要なエスケープは ensure_ascii=False で行われる
        print(json.dumps({
            'decision': 'block',
            'reason': (
                'The final response footer is missing or incorrect. Preserve the response body. '
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
