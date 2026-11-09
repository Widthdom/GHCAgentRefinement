import argparse
import io
import json
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
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))


_SHELL_TOOL_NAMES = {'bash', 'powershell'}
_DEFINITION_KEYS = ('claimPatterns', 'hedgePatterns', 'measurementCommandPatterns')
_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
_QUOTE_LINE_PATTERN = re.compile(r'^\s*>.*$', re.MULTILINE)
# 文の区切り。日本語の句点・感嘆符・疑問符、英語の文末の記号と空白、改行で区切る。
_SENTENCE_SEPARATOR_PATTERN = re.compile(r'(?<=[。！？!?])|(?<=\.)\s+|\n')
_MAX_CLAIM_LENGTH = 120
_REASON_PREFIX = 'The final response claims a performance improvement'
_BLOCK_REASON = (
    _REASON_PREFIX + ' ("{claim}"), but no benchmark, profiler, or timing command was run successfully since '
    "the user's latest request. Performance claims without measurement are guesses: the change may be slower, "
    'or the difference may be noise. Measure it now (for example hyperfine, BenchmarkDotNet, pytest-benchmark, '
    'go test -bench, cargo bench, or a timed run before and after the change) and report the actual numbers. '
    'If you cannot measure it, rephrase the claim as an unverified expectation and say that it was not measured.'
)


def _load_definitions(path):
    # 戻り値は {キー: [コンパイル済みの正規表現]}。
    with open(path, encoding='utf-8-sig') as definitions_file:
        definitions = json.load(definitions_file)
    if not isinstance(definitions, dict):
        raise ValueError('performance claim definitions must be a JSON object.')
    loaded = {}
    for key in _DEFINITION_KEYS:
        patterns = definitions.get(key)
        if not isinstance(patterns, list) or not patterns or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in patterns
        ):
            raise ValueError(f'performance claim definitions require a non-empty array of non-empty strings for {key}.')
        try:
            loaded[key] = [re.compile(pattern) for pattern in patterns]
        except re.PatternError as ex:
            raise ValueError(f'invalid regular expression in {key}: {ex}') from None
    return loaded


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・シェル実行・本hookによる差し戻し済みかを集める。
    events, already_requested = _turn_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
    # 拒否などで実行されなかった場合は終了コードが記録されないため、計測とみなさない。
    succeeded_commands = [
        arguments['command']
        for tool_name, arguments, completion in _turn_events['get_tool_calls'](events)
        if tool_name in _SHELL_TOOL_NAMES and isinstance(arguments, dict) and isinstance(arguments.get('command'), str)
        and _turn_events['get_shell_exit_code'](completion) == 0
    ]
    return _turn_events['get_last_assistant_message'](events), succeeded_commands, already_requested


def _find_claim(definitions, response):
    # 戻り値は、断定している性能改善の主張を含む最初の文。なければNone。
    # コードブロックと引用は主張とみなさない。推測・否定を含む文（「速くなるはず」「計測していない」）は除く。
    text = _QUOTE_LINE_PATTERN.sub('', _FENCED_CODE_PATTERN.sub('', response))
    for sentence in _SENTENCE_SEPARATOR_PATTERN.split(text):
        sentence = sentence.strip()
        if not sentence or not any(pattern.search(sentence) for pattern in definitions['claimPatterns']):
            continue
        if any(pattern.search(sentence) for pattern in definitions['hedgePatterns']):
            continue
        return sentence
    return None


def main(arguments=None):
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'performance-claim-definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        definitions = _load_definitions(parsed_arguments.definitions_path)
        hook_input = _hook_input['read_hook_input']()

        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0

        transcript_path = _hook_input['get_transcript_path'](hook_input)

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答とシェル実行の結果を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        last_assistant_msg, succeeded_commands, already_requested = _get_turn_summary(transcript_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect unmeasured performance claims: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、計測できない場合でも停止できるようにする。
    if last_assistant_msg is None or already_requested:
        return 0
    claim = _find_claim(definitions, last_assistant_msg)
    if claim is None:
        return 0
    if any(pattern.search(command) for command in succeeded_commands for pattern in definitions['measurementCommandPatterns']):
        return 0
    if len(claim) > _MAX_CLAIM_LENGTH:
        claim = claim[:_MAX_CLAIM_LENGTH] + '...'
    print(json.dumps(
        {'decision': 'block', 'reason': _BLOCK_REASON.format(claim=claim)}, ensure_ascii=False, separators=(',', ':'),
    ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
