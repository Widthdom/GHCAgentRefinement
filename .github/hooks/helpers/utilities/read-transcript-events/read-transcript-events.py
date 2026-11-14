import json
import time
from datetime import datetime

# セッションのトランスクリプト（events.jsonl）を読む。トランスクリプトを検査するagentStop hookが共有する。
#   read_transcript_events：イベントを1件ずつ検証して読む。
#   wait_for_agent_stop_event：agentStop hookの発動時点で、それより前のイベントが書き込まれるのを待つ。
#   get_turn_events：イベントをユーザーの依頼ごとに分ける。以降の関数は、分けた依頼のイベントから必要な情報を取り出す。
# hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。

# hook入力のtimestampとトランスクリプトのイベントの時刻は、記録する処理が異なるため前後しうる。その幅（ミリ秒）。
_CLOCK_TOLERANCE_MS = 1000


def _read_jsonl_objects(path, skip_incomplete_lines):
    # 空行を除く各行を、JSONオブジェクトとして行番号とともに返す。
    # skip_incomplete_lines：JSONとして解析できない行（非同期の追記中の不完全な行）を、異常とせず読み飛ばすか。
    with open(path, encoding='utf-8-sig') as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as exception:
                if skip_incomplete_lines:
                    continue
                raise ValueError(f'Invalid JSON at line {line_number}: {exception}') from exception
            if not isinstance(value, dict):
                raise ValueError(f'Transcript event must be a JSON object at line {line_number}.')
            yield line_number, value


def read_transcript_events(transcript_path, skip_incomplete_lines=False):
    # イベント（JSONオブジェクト）を記録された順に返す。`data` がなければ空のオブジェクトとし、オブジェクトでなければ異常とする。
    for line_number, event in _read_jsonl_objects(transcript_path, skip_incomplete_lines):
        data = event.setdefault('data', {})
        if not isinstance(data, dict):
            raise ValueError(f'Event data must be a JSON object at line {line_number}.')
        yield event


def get_event_time_ms(event):
    # イベントの時刻（ISO 8601の文字列）をUnixミリ秒にする。読めなければNone。
    # fromisoformatはPython 3.11以降、末尾のZをUTCとして解釈する。
    value = event.get('timestamp')
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return None


def _is_agent_stop_event_recorded(transcript_path, hook_timestamp):
    # トランスクリプトに、今回実行中のagentStop hookのhook.startイベントが書き込まれているかを返す。
    #   過去のターンのagentStopと取り違えないよう、イベント自体の時刻がhook入力のtimestamp以降（幅を見込む）のものを対象とする。
    #   イベントの時刻を読めない場合だけ、hook.startのinputのtimestampがhook入力と一致するものを対象とする。
    #   hook.startのinputは標準入力と形式が異なる場合があるため、inputでの照合は補助にとどめる。
    for event in read_transcript_events(transcript_path, skip_incomplete_lines=True):
        if event.get('type') != 'hook.start' or event['data'].get('hookType') != 'agentStop':
            continue
        event_time_ms = get_event_time_ms(event)
        if event_time_ms is not None and event_time_ms >= hook_timestamp - _CLOCK_TOLERANCE_MS:
            return True
        # hook入力の形はCopilot側の仕様に依存するため、想定外の形でも異常とはせず対象外とする。
        hook_input = event['data'].get('input')
        if isinstance(hook_input, dict) and hook_input.get('timestamp') == hook_timestamp:
            return True
    return False


def wait_for_agent_stop_event(transcript_path, hook_timestamp, timeout_seconds=5.0):
    # GitHub Copilot はトランスクリプトに非同期で追記するため、agentStop hookが起動した直後は、最終応答がまだ書き込まれていない可能性がある。
    # イベントは発生順に追記されるので、自身のhook.startが追記されれば、それより前に発生した最終応答も書き込み済みとみなす。
    # 期限を過ぎた場合は待機をやめ、その時点の内容で呼び出し元の処理を続ける。
    # timestampが数値でなければ照合できないため待たない。boolはintのサブクラスなので除く。
    if not isinstance(hook_timestamp, (int, float)) or isinstance(hook_timestamp, bool):
        return
    deadline = time.monotonic() + timeout_seconds
    while not _is_agent_stop_event_recorded(transcript_path, hook_timestamp):
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)


def get_turn_events(transcript_path):
    # イベントを (種類, data) の組にし、依頼ごとのリストに分けて返す。
    # 先頭の要素は最初の依頼より前のイベント、以降の要素は依頼のuser.messageから始まる。要素は常に1つ以上ある。
    turns = [[]]
    for event in read_transcript_events(transcript_path):
        event_type, data = event.get('type'), event['data']
        if event_type == 'user.message' and data.get('delivery') != 'queued':
            turns.append([])
        turns[-1].append((event_type, data))
    return turns


def count_requests_by_hook(events, reason_prefix):
    # 依頼のイベントの中で、reason_prefixで始まるhookの差し戻し（queuedのuser.message）の数を返す。
    return sum(
        1 for event_type, data in events
        if event_type == 'user.message' and data.get('delivery') == 'queued'
        and isinstance(data.get('content'), str) and data['content'].startswith(reason_prefix)
    )


def is_already_requested(events, reason_prefix):
    # 依頼のイベントの中に、reason_prefixで始まるhookの差し戻しがあるか（hookが差し戻し済みか）を返す。
    return count_requests_by_hook(events, reason_prefix) > 0


def get_latest_turn(transcript_path, reason_prefix):
    # ユーザーの最新の依頼以降のイベントと、reason_prefixで始まる差し戻しで本hookが差し戻し済みかを返す。
    events = get_turn_events(transcript_path)[-1]
    return events, is_already_requested(events, reason_prefix)


def get_request(events):
    # 依頼の文を返す。依頼のuser.messageから始まらない場合と、文字列でない場合はNone。
    if events and events[0][0] == 'user.message' and events[0][1].get('delivery') != 'queued':
        content = events[0][1].get('content')
        return content if isinstance(content, str) else None
    return None


def get_request_texts(events):
    # 依頼と、ユーザーの割り込みのuser.messageの文を、記録された順に返す。文字列でないものは除く。
    # hookの差し戻しは、agentStopのhook.endが出力した `reason` で始まる `queued` のuser.messageとして記録されるため除く。
    block_reasons = []
    texts = []
    for event_type, data in events:
        if event_type == 'hook.end' and data.get('hookType') == 'agentStop':
            output = data.get('output')
            reason = output.get('reason') if isinstance(output, dict) and output.get('decision') == 'block' else None
            if isinstance(reason, str) and reason:
                block_reasons.append(reason)
        elif event_type == 'user.message':
            content = data.get('content')
            if not isinstance(content, str):
                continue
            if data.get('delivery') == 'queued' and any(content.startswith(reason) for reason in block_reasons):
                continue
            texts.append(content)
    return texts


def get_last_assistant_message(events):
    # 空白以外を含む最後のassistant.messageの文を返す。なければNone。
    last_assistant_msg = None
    for event_type, data in events:
        content = data.get('content')
        if event_type == 'assistant.message' and isinstance(content, str) and content.strip():
            last_assistant_msg = content
    return last_assistant_msg


def get_tool_calls(events):
    # ツール呼び出しを、完了した順に (ツール名, 引数, 完了のdata) の組のリストで返す。
    # ツール名は `functions.` 接頭辞を外す。引数は記録されたまま返す（JSON文字列の場合もある）。
    # tool.execution_startとtool.execution_completeは `toolCallId` で対応づける。完了していない呼び出しは、完了のdataをNoneとして最後に加える。
    started = {}
    calls = []
    for event_type, data in events:
        call_id = data.get('toolCallId')
        if event_type == 'tool.execution_start':
            tool_name = data.get('toolName')
            if isinstance(call_id, str) and isinstance(tool_name, str):
                started[call_id] = (tool_name.removeprefix('functions.'), data.get('arguments'))
        elif event_type == 'tool.execution_complete' and isinstance(call_id, str) and call_id in started:
            calls.append((*started.pop(call_id), data))
    calls.extend((tool_name, arguments, None) for tool_name, arguments in started.values())
    return calls


def get_shell_exit_code(completion):
    # シェル実行の完了のdataから終了コードを返す。拒否などで実行されなかった場合や、完了していない場合はNone。
    shell_execution = completion.get('shellExecution') if isinstance(completion, dict) else None
    exit_code = shell_execution.get('exitCode') if isinstance(shell_execution, dict) else None
    return exit_code if type(exit_code) is int else None
