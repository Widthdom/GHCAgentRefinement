import io
import os
import re
import runpy
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_truncate_text = runpy.run_path(os.path.join(_utilities_directory, 'truncate-text', 'truncate-text.py'))['truncate_text']
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))


# 作業を後回しにすると約束する表現。エージェントには応答の後に作業する機会がないため、果たされない。
_JAPANESE_DEFERRAL = r'(?:後で|あとで|後ほど|のちほど|追って|別途|今後|次回|次のステップで|別の(?:PR|プルリクエスト|コミット|タスク|作業)で)'
_JAPANESE_ACTION = r'(?:対応|修正|直|追加|実装|確認|検討|調査|整理|対処|更新|改善|リファクタ|書き換え|見直)'
_JAPANESE_VOLITION = r'(?:す|そう|したい|しよう|しましょう|して(?:いく|いきます|おく|おきます)|予定)'
_ENGLISH_ACTION = r'(?:fix|address|handle|add|implement|update|clean\s+up|revisit|look\s+into|investigate|refactor|tackle|cover|follow\s+up)'
_ENGLISH_DEFERRAL = r'(?:later|in\s+a\s+follow-?up|separately|next\s+time|in\s+a\s+(?:future|separate|later|subsequent)\s+\w+|afterwards)'
_PROMISE_PATTERNS = (
    re.compile(_JAPANESE_DEFERRAL + r'[^。\n]{0,20}?' + _JAPANESE_ACTION + r'[^。\n]{0,4}?' + _JAPANESE_VOLITION),
    re.compile(
        r"\b(?:I|we)(?:'ll|\s+will|\s+(?:plan|intend|am\s+going|are\s+going)\s+to)\s+(?:\w+\s+){0,2}?" + _ENGLISH_ACTION
        + r'\b[^.\n]{0,60}?\b' + _ENGLISH_DEFERRAL + r'\b',
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:I|we)(?:'ll|\s+will)\s+follow\s+up\b", re.IGNORECASE),
    re.compile(r'\bwill\s+be\s+(?:addressed|handled|fixed|added|implemented|covered)\s+' + _ENGLISH_DEFERRAL + r'\b', re.IGNORECASE),
)
# ユーザーの判断に委ねる申し出（「必要なら後で追加する」「if you want」など）や、要否の説明は約束とみなさない。
_NON_PROMISE_PATTERN = re.compile(
    r'(?:必要|希望|要望|指示|よけれ|良けれ)[^。\n]{0,6}?(?:なら|があれば|あれば|ば)|かどうか|必要は(?:ない|ありません)|不要|[?？]'
    r'|\bif\s+(?:you|needed|necessary|desired|requested|wanted)\b|\bwould\s+you\b|\bshould\s+I\b|\blet\s+me\s+know\b',
    re.IGNORECASE,
)
# 日本語の句点・疑問符の後と、英語の文末の `.` に続く空白で文を分ける。`README.md` のような語中の `.` では分けない。
_SENTENCE_BOUNDARY_PATTERN = re.compile(r'(?<=[。！？!?])|(?<=\.)\s+')
_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
# インラインコードと引用（「」・『』・""・“”）。表現を例として挙げているだけで、約束ではない。
_QUOTED_PATTERN = re.compile(r'`[^`\n]*`|「[^」\n]*」|『[^』\n]*』|"[^"\n]*"|“[^”\n]*”')
_FIXME_PATTERN = re.compile(r'\bFIXME:')
_REASON_PREFIX = 'The final response promises work for later'
_MAX_LISTED_PROMISES = 5
_MAX_PROMISE_LENGTH = 200


def _get_turn(transcript_path, extract_edit_tool_changes):
    # ユーザーの最新の依頼以降の最終応答と、FIXMEコメントを書き込んだかを返す。本hookによる差し戻し済みかも返す。
    events, already_requested = _turn_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
    wrote_fixme = any(
        completion is not None and completion.get('success') is True
        and any(_FIXME_PATTERN.search(change['written']) for change in extract_edit_tool_changes(tool_name, arguments))
        for tool_name, arguments, completion in _turn_events['get_tool_calls'](events)
    )
    return _turn_events['get_last_assistant_message'](events), wrote_fixme, already_requested


def _find_promises(response):
    # コードブロック・インラインコード・引用を除き、約束を含み申し出ではない文を返す。
    text = _QUOTED_PATTERN.sub('', _FENCED_CODE_PATTERN.sub('', response))
    promises = []
    for line in text.splitlines():
        for sentence in _SENTENCE_BOUNDARY_PATTERN.split(line):
            sentence = sentence.strip()
            if (sentence and any(pattern.search(sentence) for pattern in _PROMISE_PATTERNS)
                    and not _NON_PROMISE_PATTERN.search(sentence)):
                promises.append(sentence)
    return promises


def _get_block_reason(promises):
    lines = [
        f'{_REASON_PREFIX}, but you will not get another chance to work on it after this response ends. '
        'These sentences defer work without doing it:'
    ]
    for promise in promises[:_MAX_LISTED_PROMISES]:
        lines.append(f'- {_truncate_text(promise, _MAX_PROMISE_LENGTH)}')
    if len(promises) > _MAX_LISTED_PROMISES:
        lines.append(f'- ... and {len(promises) - _MAX_LISTED_PROMISES} more')
    lines.append(
        'Do the deferred work now if it is within the request. Otherwise, rewrite the final response to state plainly '
        'that it was not done and why, ask the user whether it should be done, or leave a `FIXME:` comment '
        'at the relevant code and report where you added it.'
    )
    return '\n'.join(lines)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0
        transcript_path = _hook_input['get_transcript_path'](hook_input)

        # 編集ツールの引数の解析は、共通utilityで行う。
        extract_edit_tool_changes = runpy.run_path(
            os.path.join(_utilities_directory, 'edit-tool-changes', 'edit-tool-changes.py')
        )['extract_edit_tool_changes']
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, wrote_fixme, already_requested = _get_turn(transcript_path, extract_edit_tool_changes)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect deferred promises: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、言い換えても検出され続けて停止できなくなるのを防ぐ。
    # FIXMEコメントを書き込んだ依頼では、後回しにした作業をコードに残したとみなす。
    if last_assistant_msg is None or already_requested or wrote_fixme:
        return 0

    promises = _find_promises(last_assistant_msg)
    if promises:
        _hook_output['write_block_decision'](_get_block_reason(promises))
    return 0


if __name__ == '__main__':
    sys.exit(main())
