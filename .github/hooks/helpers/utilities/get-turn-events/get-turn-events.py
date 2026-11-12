import os
import runpy

# トランスクリプトをユーザーの依頼ごとに分ける。依頼の範囲を検査するagentStop hookが共有する。
# hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。

_read_jsonl_objects = runpy.run_path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'read-jsonl-objects', 'read-jsonl-objects.py',
))['read_jsonl_objects']


def get_turns(transcript_path):
    # イベントを (種類, data) の組にし、依頼ごとのリストに分けて返す。
    # 先頭の要素は最初の依頼より前のイベント、以降の要素は依頼のuser.messageから始まる。要素は常に1つ以上ある。
    turns = [[]]
    for event in _read_jsonl_objects(transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        event_type = event.get('type')
        if event_type == 'user.message' and data.get('delivery') != 'queued':
            turns.append([])
        turns[-1].append((event_type, data))
    return turns


def is_already_requested(events, reason_prefix):
    # 依頼のイベントの中に、reason_prefixで始まるhookの差し戻しがあるか（hookが差し戻し済みか）を返す。
    return any(
        event_type == 'user.message' and data.get('delivery') == 'queued'
        and isinstance(data.get('content'), str) and data['content'].startswith(reason_prefix)
        for event_type, data in events
    )


def get_latest_turn(transcript_path, reason_prefix):
    # ユーザーの最新の依頼以降のイベントと、reason_prefixで始まる差し戻しで本hookが差し戻し済みかを返す。
    events = get_turns(transcript_path)[-1]
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
