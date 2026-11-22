import argparse
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
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read_hook_input.py'))
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write_hook_output.py'))
_truncate_text = runpy.run_path(os.path.join(_utilities_directory, 'truncate-text', 'truncate_text.py'))['truncate_text']
_tool_calls = runpy.run_path(os.path.join(_utilities_directory, 'parse-tool-calls', 'parse_tool_calls.py'))
_definitions = runpy.run_path(os.path.join(_utilities_directory, 'read-definitions', 'read_definitions.py'))
_transcript_events = runpy.run_path(os.path.join(_utilities_directory, 'read-transcript-events', 'read_transcript_events.py'))


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
    label = 'performance claim definitions'
    definitions = _definitions['read_definitions'](path, label)
    return {key: _definitions['get_regex_matchers'](definitions, key, label) for key in _DEFINITION_KEYS}


def _get_turn_summary(transcript_path):
    # ユーザーの最新の依頼以降の、最終応答・シェル実行・本hookによる差し戻し済みかを集める。
    events, already_requested = _transcript_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
    # 拒否などで実行されなかった場合は終了コードが記録されないため、計測とみなさない。
    # 引数はJSON文字列で記録される場合もあるため、包みを外してから読む。
    succeeded_commands = []
    for tool_name, arguments, completion in _transcript_events['get_tool_calls'](events):
        arguments = _tool_calls['unwrap_tool_args'](arguments)
        command = arguments.get('command') if isinstance(arguments, dict) else None
        if (_tool_calls['is_shell_tool'](tool_name) and isinstance(command, str)
                and _transcript_events['get_shell_exit_code'](completion) == 0):
            succeeded_commands.append(command)
    return _transcript_events['get_last_assistant_message'](events), succeeded_commands, already_requested


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
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
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
        _transcript_events['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

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
    _hook_output['write_block_decision'](_BLOCK_REASON.format(claim=_truncate_text(claim, _MAX_CLAIM_LENGTH)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
