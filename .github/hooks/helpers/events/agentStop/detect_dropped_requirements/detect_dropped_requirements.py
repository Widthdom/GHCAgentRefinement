import argparse
import os
import re
import sys


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'read_definitions'),
    os.path.join(_utilities_directory, 'write_hook_output'),
    os.path.join(_utilities_directory, 'truncate_text'),
    os.path.join(_utilities_directory, 'read_transcript_events'),
]
import read_hook_input as _hook_input
import read_definitions as _definitions
import write_hook_output as _hook_output
from truncate_text import truncate_text as _truncate_text
import read_transcript_events as _transcript_events


# 定義ファイルのキー。stopWordsは、どの項目にも現れやすく項目を見分けられないため、キーワードにしない語。
_STOP_WORDS_KEY = 'stopWords'
_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
# 箇条書き・番号付きの項目。グループは (字下げ, 本文)。
#   記号：- * + • ・、番号：1. 1) 1）、丸数字：①〜⑳
#   全角の記号（・ 1） ①）は直後の空白を省略して書かれることが多いため、空白を必須にしない。
_LIST_ITEM_PATTERN = re.compile(r'^([ \t]*)(?:(?:[-*+•]|\d{1,2}[.)])[ \t]+|(?:・|\d{1,2}）|[①-⑳])[ \t]*)(\S.*?)\s*$')
_INLINE_CODE_PATTERN = re.compile(r'`([^`\n]+)`')
# 項目のキーワード。英数字の識別子・パス、カタカナ語、漢字の熟語。
#   ァ-ヺ：ァ〜ヺ、ー：ー、一-鿿：CJK統合漢字、々：々
_KEYWORD_PATTERN = re.compile(
    r'[A-Za-z][A-Za-z0-9_.\-/]*[A-Za-z0-9]|[ァ-ヺー]{2,}|[一-鿿々]{2,}'
)
_MIN_ASCII_KEYWORD_LENGTH = 3
# 1項目だけの箇条書きは依頼の列挙とみなさない。
_MIN_ITEMS = 2
_MAX_ITEMS = 20
_MAX_LISTED_ITEMS = 5
_MAX_DISPLAY_LENGTH = 60
_REASON_PREFIX = 'The final response does not mention'
_REASON = (
    f'{_REASON_PREFIX} {{count}} of the {{total}} items the user listed: {{items}}. '
    'Do the missing items now, or state explicitly in the final response that each one was done, '
    'was not done and why, or needs a decision from the user. Do not drop a requested item silently.'
)


def _load_stop_words(definitions_path):
    label = 'dropped requirement definitions'
    definitions = _definitions.read_definitions(definitions_path, label)
    return {word.strip().casefold() for word in _definitions.get_string_list(definitions, _STOP_WORDS_KEY, label)}


def _get_items(request):
    # コードブロックを除いた依頼から、最も浅い字下げの項目だけを取り出す。深い項目は親の項目の詳細とみなす。
    matches = [_LIST_ITEM_PATTERN.match(line) for line in _FENCED_CODE_PATTERN.sub('', request).splitlines()]
    matches = [match for match in matches if match]
    if not matches:
        return []
    indent = min(len(match.group(1).expandtabs(4)) for match in matches)
    return [match.group(2) for match in matches if len(match.group(1).expandtabs(4)) == indent][:_MAX_ITEMS]


def _get_keywords(item, stop_words):
    keywords = {code.strip().casefold() for code in _INLINE_CODE_PATTERN.findall(item) if code.strip()}
    for word in _KEYWORD_PATTERN.findall(_INLINE_CODE_PATTERN.sub(' ', item)):
        if word.isascii() and len(word) < _MIN_ASCII_KEYWORD_LENGTH:
            continue
        keywords.add(word.casefold())
    return keywords - stop_words


def _find_dropped_items(request, response, stop_words):
    # 戻り値は (最終応答にキーワードが1つも現れない項目, 項目の総数)。
    # キーワードを持たない項目は判定できないため、触れたものとみなす。
    items = _get_items(request)
    if len(items) < _MIN_ITEMS:
        return [], len(items)
    folded_response = response.casefold()
    dropped = [
        item for item in items
        if (keywords := _get_keywords(item, stop_words)) and not any(keyword in folded_response for keyword in keywords)
    ]
    return dropped, len(items)


def main():
    _hook_output.configure_output_streams()

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        dest='defs_path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'requirement_definitions.json'),
    )
    parsed_args = parser.parse_args()

    try:
        stop_words = _load_stop_words(parsed_args.defs_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to load requirement definitions: {ex}', file=sys.stderr)
        return 2

    try:
        hook_input = _hook_input.read_hook_input()
        # サブエージェントの停止は対象外。ユーザーの依頼に最終応答を返すのは親のため。
        if hook_input.get('agentId'):
            return 0
        transcript_path = _hook_input.get_transcript_path(hook_input)

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答を書き込み済みにする。
        _transcript_events.wait_for_agent_stop_event(transcript_path, hook_input.get('timestamp'))

        # ユーザーの最新の依頼、以降の最終応答、本hookによる差し戻し済みかを集める。
        events, already_requested = _transcript_events.get_latest_turn(transcript_path, _REASON_PREFIX)
        latest_request = _transcript_events.get_request(events)
        last_assistant_msg = _transcript_events.get_last_assistant_message(events)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect dropped requirements: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、判定を誤った場合に停止できなくなるのを防ぐ。
    if already_requested or latest_request is None or last_assistant_msg is None:
        return 0

    dropped, total = _find_dropped_items(latest_request, last_assistant_msg, stop_words)
    if dropped:
        items = '; '.join(
            f'"{_truncate_text(item, _MAX_DISPLAY_LENGTH)}"' for item in dropped[:_MAX_LISTED_ITEMS]
        )
        if len(dropped) > _MAX_LISTED_ITEMS:
            items += f'; and {len(dropped) - _MAX_LISTED_ITEMS} more'
        reason = _REASON.format(count=len(dropped), total=total, items=items)
        _hook_output.write_block_decision(reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
