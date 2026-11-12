import json
import time
from datetime import datetime

# hook入力のtimestampとトランスクリプトのイベントの時刻は、記録する処理が異なるため前後しうる。その幅（ミリ秒）。
_CLOCK_TOLERANCE_MS = 1000


def _get_event_time_ms(event):
    # イベントの時刻（ISO 8601の文字列）をUnixミリ秒にする。読めなければNone。
    value = event.get('timestamp')
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp() * 1000
    except ValueError:
        return None


def _is_agent_stop_event_recorded(transcript_path, hook_timestamp):
    # トランスクリプトに、今回実行中のagentStop hookのhook.startイベントが書き込まれているかを返す。
    #   過去のターンのagentStopと取り違えないよう、イベント自体の時刻がhook入力のtimestamp以降（幅を見込む）のものを対象とする。
    #   イベントの時刻を読めない場合だけ、hook.startのinputのtimestampがhook入力と一致するものを対象とする。
    #   hook.startのinputは標準入力と形式が異なる場合があるため、inputでの照合は補助にとどめる。

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

            event_time_ms = _get_event_time_ms(event)
            if event_time_ms is not None and event_time_ms >= hook_timestamp - _CLOCK_TOLERANCE_MS:
                return True

            # hook入力の形はCopilot側の仕様に依存するため、想定外の形でも異常とはせず対象外とする。
            hook_input = data.get('input')
            if not isinstance(hook_input, dict):
                continue

            if hook_input.get('timestamp') == hook_timestamp:
                return True

    return False


def wait_for_agent_stop_event(transcript_path, hook_timestamp, timeout_seconds=5.0):
    # GitHub Copilot はトランスクリプトに非同期で追記するため、agentStop hookが起動した直後は、最終応答がまだ書き込まれていない可能性がある。
    # イベントは発生順に追記されるので、自身のhook.startが追記されれば、それより前に発生した最終応答も書き込み済みとみなすことができる。
    # 期限を過ぎた場合は待機をやめ、その時点の内容で呼び出し元の処理を続ける。

    # Pythonでは bool が int のサブクラスになっているので、その分を除外する必要がある。
    # timestampが数値でなければ照合できないため待たない。
    if not isinstance(hook_timestamp, (int, float)) or isinstance(hook_timestamp, bool):
        return

    # monotonic：システムクロックに依存せず、単調増加する時刻を返す。タイムアウト判定に使用する。
    deadline = time.monotonic() + timeout_seconds
    # _is_agent_stop_event_recorded() が True を返すまで、
    # timeout_seconds秒の間0.05秒ごとにhook.startの書き込みを確認するループを開始する。
    while not _is_agent_stop_event_recorded(transcript_path, hook_timestamp):
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
