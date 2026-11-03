import argparse
import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


# 定義ファイルのキー。stopWordsは、どの項目にも現れやすく項目を見分けられないため、キーワードにしない語。
STOP_WORDS_KEY = 'stopWords'
FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
# 箇条書き・番号付きの項目。グループは (字下げ, 本文)。
#   記号：- * + • ・、番号：1. 1) 1）、丸数字：①〜⑳
#   全角の記号（・ 1） ①）は直後の空白を省略して書かれることが多いため、空白を必須にしない。
LIST_ITEM_PATTERN = re.compile(r'^([ \t]*)(?:(?:[-*+•]|\d{1,2}[.)])[ \t]+|(?:・|\d{1,2}）|[①-⑳])[ \t]*)(\S.*?)\s*$')
INLINE_CODE_PATTERN = re.compile(r'`([^`\n]+)`')
# 項目のキーワード。英数字の識別子・パス、カタカナ語、漢字の熟語。
#   ァ-ヺ：ァ〜ヺ、ー：ー、一-鿿：CJK統合漢字、々：々
KEYWORD_PATTERN = re.compile(
    r'[A-Za-z][A-Za-z0-9_.\-/]*[A-Za-z0-9]|[ァ-ヺー]{2,}|[一-鿿々]{2,}'
)
MIN_ASCII_KEYWORD_LENGTH = 3
# 1項目だけの箇条書きは依頼の列挙とみなさない。
MIN_ITEMS = 2
MAX_ITEMS = 20
MAX_LISTED_ITEMS = 5
MAX_DISPLAY_LENGTH = 60
REASON_PREFIX = 'The final response does not mention'
REASON = (
    f'{REASON_PREFIX} {{count}} of the {{total}} items the user listed: {{items}}. '
    'Do the missing items now, or state explicitly in the final response that each one was done, '
    'was not done and why, or needs a decision from the user. Do not drop a requested item silently.'
)


def is_non_empty_string_list(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, str) and bool(item.strip()) for item in value))


def load_stop_words(definitions_path):
    # 定義ファイルはUTF-8のJSONオブジェクトとして読み込む。UTF-8 BOMは許容する。
    if not os.path.isfile(definitions_path):
        raise ValueError(f'Definitions path is not a file or could not be verified as a file: {definitions_path}')
    try:
        with open(definitions_path, encoding='utf-8-sig') as file:
            definitions = json.load(file)
    except json.JSONDecodeError as ex:
        raise ValueError(f'Definitions are not valid JSON: {ex}') from ex
    if not isinstance(definitions, dict):
        raise ValueError('Definitions must be a JSON object.')
    if not is_non_empty_string_list(definitions.get(STOP_WORDS_KEY)):
        raise ValueError(f'Definitions require a non-empty array of non-empty strings for {STOP_WORDS_KEY}.')
    return {word.strip().casefold() for word in definitions[STOP_WORDS_KEY]}


def get_turn_summary(transcript_path):
    # ユーザーの最新の依頼、以降の最終応答、本hookによる差し戻し済みかを集める。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    latest_request = None
    last_assistant_msg = None
    already_requested = False
    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects' / 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        event_type = event.get('type')

        if event_type == 'user.message':
            content = data.get('content')
            if data.get('delivery') != 'queued':
                latest_request = content if isinstance(content, str) else None
                last_assistant_msg = None
                already_requested = False
            elif isinstance(content, str) and content.startswith(REASON_PREFIX):
                already_requested = True
        elif event_type == 'assistant.message':
            content = data.get('content')
            if isinstance(content, str) and content.strip():
                last_assistant_msg = content

    return latest_request, last_assistant_msg, already_requested


def get_items(request):
    # コードブロックを除いた依頼から、最も浅い字下げの項目だけを取り出す。深い項目は親の項目の詳細とみなす。
    matches = [LIST_ITEM_PATTERN.match(line) for line in FENCED_CODE_PATTERN.sub('', request).splitlines()]
    matches = [match for match in matches if match]
    if not matches:
        return []
    indent = min(len(match.group(1).expandtabs(4)) for match in matches)
    return [match.group(2) for match in matches if len(match.group(1).expandtabs(4)) == indent][:MAX_ITEMS]


def get_keywords(item, stop_words):
    keywords = {code.strip().casefold() for code in INLINE_CODE_PATTERN.findall(item) if code.strip()}
    for word in KEYWORD_PATTERN.findall(INLINE_CODE_PATTERN.sub(' ', item)):
        if word.isascii() and len(word) < MIN_ASCII_KEYWORD_LENGTH:
            continue
        keywords.add(word.casefold())
    return keywords - stop_words


def find_dropped_items(request, response, stop_words):
    # 戻り値は (最終応答にキーワードが1つも現れない項目, 項目の総数)。
    # キーワードを持たない項目は判定できないため、触れたものとみなす。
    items = get_items(request)
    if len(items) < MIN_ITEMS:
        return [], len(items)
    folded_response = response.casefold()
    dropped = [
        item for item in items
        if (keywords := get_keywords(item, stop_words)) and not any(keyword in folded_response for keyword in keywords)
    ]
    return dropped, len(items)


def shorten(value):
    return value if len(value) <= MAX_DISPLAY_LENGTH else value[:MAX_DISPLAY_LENGTH] + '...'


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        dest='defs_path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'requirement-definitions.json'),
    )
    parsed_args = parser.parse_args()

    try:
        stop_words = load_stop_words(parsed_args.defs_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to load requirement definitions: {ex}', file=sys.stderr)
        return 2

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            print('agentStop hook input is empty.', file=sys.stderr)
            return 2
        hook_input = json.loads(hook_input_json)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f'Failed to parse agentStop hook input: {ex}', file=sys.stderr)
        return 2

    if not isinstance(hook_input, dict):
        print('agentStop hook input must be a JSON object.', file=sys.stderr)
        return 2

    # サブエージェントの停止は対象外。ユーザーの依頼に最終応答を返すのは親のため。
    if hook_input.get('agentId'):
        return 0

    transcript_path = hook_input.get('transcriptPath')
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        print('agentStop hook transcriptPath must be a non-empty string.', file=sys.stderr)
        return 2

    if not os.path.isfile(transcript_path):
        print(f'Transcript path is not a file or could not be verified as a file: {transcript_path}', file=sys.stderr)
        return 2

    try:
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答を書き込み済みにする。
        runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event' / 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        latest_request, last_assistant_msg, already_requested = get_turn_summary(transcript_path)
    except (OSError, ValueError) as ex:
        print(f'Failed to inspect dropped requirements: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、判定を誤った場合に停止できなくなるのを防ぐ。
    if already_requested or latest_request is None or last_assistant_msg is None:
        return 0

    dropped, total = find_dropped_items(latest_request, last_assistant_msg, stop_words)
    if dropped:
        items = '; '.join(f'"{shorten(item)}"' for item in dropped[:MAX_LISTED_ITEMS])
        if len(dropped) > MAX_LISTED_ITEMS:
            items += f'; and {len(dropped) - MAX_LISTED_ITEMS} more'
        reason = REASON.format(count=len(dropped), total=total, items=items)
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
