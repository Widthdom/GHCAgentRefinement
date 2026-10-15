import os
import sys
import tempfile

from common import (
    equals_ignore_case,
    get_property,
    is_null_or_white_space,
    load_helper,
    parse_json,
    parse_json_enumerated,
    read_all_text,
    read_lines,
    to_compressed_json,
    to_ps_string,
    try_parse_guid,
)


def write_error(message):
    print(message, file=sys.stderr)


def main():
    # 標準出力・標準エラーをUTF-8に統一することで、JSON内の日本語などを正しく出力する。BOMは出力されない。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    # agentStopのタイミングで本スクリプトの呼び出し元であるCopilotがPythonプロセスを起動し
    # 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
    # このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
    #   改行コードを変換せずに扱うため、バイト列として読み取ってからUTF-8として復号化する。
    input_json = sys.stdin.buffer.read().decode('utf-8', errors='replace')
    if is_null_or_white_space(input_json):
        write_error('agentStop hook input is empty.')
        return 2

    # 読み取ったJSONをPythonのオブジェクトに変換する。
    #   最外側が[]のものは拒否したいので、配列は展開せずそのまま保持する。
    try:
        hook_input = parse_json(input_json)
    except Exception as exception:
        write_error(f'Failed to parse agentStop hook input: {exception}')
        return 2

    # 変換結果がJSONオブジェクトでなければ異常終了。
    #   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
    if not isinstance(hook_input, dict):
        write_error('agentStop hook input must be a JSON object.')
        return 2

    # トランスクリプトファイル（セッション中の会話やイベントを記録したログファイル）の内容を取得する。
    #   agentStop の入力内容そのままでは情報が不足している。
    #   入力内容にはトランスクリプトファイルのパスが含まれているので、それを利用して追加情報を取得する。
    transcript_path = to_ps_string(get_property(hook_input, 'transcriptPath'))

    # トランスクリプトファイルの存在確認
    if is_null_or_white_space(transcript_path) or not os.path.isfile(transcript_path):
        write_error(f'Transcript file not found: {transcript_path}')
        return 2

    session_id = get_property(hook_input, 'sessionId')
    parsed_session_id = try_parse_guid(session_id)
    if parsed_session_id is None:
        write_error(f'Invalid session ID: {to_ps_string(session_id)}')
        return 2

    # パス区切りはOS依存のため、os.path.joinで階層ごとに連結する。
    debug_state_file_path = os.path.join(
        os.path.join(os.path.join(tempfile.gettempdir(), 'ghc-agent-refinement'), 'debug-mode'),
        f'{parsed_session_id}.json',
    )

    # デバッグモード用の状態ファイルが存在しない場合はデバッグモードでないものとみなし、処理を終了する。
    if not os.path.isfile(debug_state_file_path):
        return 0

    try:
        # 最外側が[]である配列であったとしても展開して扱う。
        debug_state = parse_json_enumerated(read_all_text(debug_state_file_path))
    except Exception as exception:
        write_error(f'Failed to parse footer debug-mode state: {exception}')
        return 2

    # enabled が真偽値（boolean）でない場合は想定外の値とみなし、通常対話を妨げないよう安全側に倒して処理を終了する。
    enabled = get_property(debug_state, 'enabled')
    if not isinstance(enabled, bool):
        return 0

    # デバッグモードが有効でなければ、処理を終了
    if enabled is not True:
        return 0

    last_assistant_message = None

    # トランスクリプトファイルに記録されているエージェントの発話（assistant.message）のうち、最後のものを取得する。
    for line in read_lines(transcript_path):
        if is_null_or_white_space(line):
            continue

        try:
            # 最外側が[]である配列であったとしても展開して扱う。
            event = parse_json_enumerated(line)
        except Exception as exception:
            write_error(f'Failed to parse transcript event: {exception}')
            return 2

        content = get_property(get_property(event, 'data'), 'content')
        if (equals_ignore_case(get_property(event, 'type'), 'assistant.message') and
                isinstance(content, str) and
                not is_null_or_white_space(content)):
            last_assistant_message = content

    # この時点で last_assistant_message に何も記録されていないということは、
    # トランスクリプトにアシスタントの応答が存在しなかったことを意味するが、
    # これは異常扱いとする。
    #   1. なぜ異常事態と言えるのか
    #     発動タイミング:
    #       このhookは agentStop で実行される。通常の会話ターンであれば、
    #       エージェントは何かしらの発話（assistant.message）を行って停止するため、
    #       ログにアシスタントメッセージが1件も存在しない状態は本来あり得ない.
    #     hookの目的:
    #       このhookの責務は「アシスタントの最終応答を取得し、
    #       デバッグフッタが正しく付与されているかを検証・強制すること」にある。
    #       対象となる応答がそもそも存在しない以上、検査自体が成立しないため
    #       exit 2（処理不能な異常）として扱うのが適切である。
    #   2. 例外的に起こり得るエッジケース
    #     完全にあり得ないわけではなく、以下のような特殊なケースでは発生し得る。
    #       - ユーザーがプロンプトを送信した直後にキャンセル操作を行った場合
    #       - エージェントがメッセージを1文字も出力する前に, 内部エラーや致命的例外で中断した場合
    #     ただし、これらはいずれも「正常に対話・応答が完了した状態」ではないため、
    #     hook側としては例外・異常事態として exit 2 で終了させる設計になっている.
    if is_null_or_white_space(last_assistant_message):
        write_error('No assistant response was found in the transcript.')
        return 2

    # 現在のJSTを取得
    current_jst = load_helper('get-current-jst').get_current_jst()

    # エージェントが使用したhookを取得（長さ0の場合は 'none' に置き換える）
    used_hooks = load_helper('get-used-hooks').get_used_hooks(
        transcript_path,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
    resolved_used_hooks = ['none'] if len(used_hooks) == 0 else used_hooks

    # エージェントが使用したskillを取得（長さ0の場合は 'none' に置き換える）
    used_skills = load_helper('get-used-skills').get_used_skills(transcript_path)
    resolved_used_skills = ['none'] if len(used_skills) == 0 else used_skills

    # フッタを整形して取得する。
    formatted_footer = load_helper('format-response-footer').format_response_footer(
        resolved_used_hooks,
        resolved_used_skills,
        current_jst,
    )

    # フッタが正しいかどうかを検証する。
    is_footer_valid = load_helper('test-response-footer').test_response_footer(
        last_assistant_message,
        resolved_used_hooks,
        resolved_used_skills,
        current_jst,
    )

    if not is_footer_valid:
        output = to_compressed_json({
            'decision': 'block',
            'reason': (
                'The final response footer is missing or incorrect. Preserve the response body. '
                'Replace any existing footer blocks with the exact block below; if no footer exists, '
                'append it to the end. The corrected response must contain exactly one footer block:\n'
                '\n'
                f'{formatted_footer}'
            ),
        })

        # 余計な出力を混ぜず、純粋な JSON 文字列を確実に渡すため、明示的に1行だけ出力する。
        print(output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
