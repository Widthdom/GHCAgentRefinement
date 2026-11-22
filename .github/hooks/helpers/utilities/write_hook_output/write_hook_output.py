import io
import json
import sys
from typing import cast

# hookの標準出力・標準エラーを設定し、判定や助言をCopilot SDKが解釈するトップレベルのJSONとして標準出力に書く。
# すべてのhookが共有する。判定や助言は正常な介入なので、呼び出し元のhookは終了コード0で終える。
# 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference


def configure_output_streams():
    # 標準出力・標準エラーを、OSやロケールによらずBOMなしのUTF-8、改行 `\n` にする。hookは出力の前に最初に呼ぶ。
    # errors='replace'：UTF-8で表せない文字（孤立したサロゲート）は、例外にせず `?` に置き換える。
    # 型定義上の `sys.stdout`・`sys.stderr` はreconfigureを持たないTextIOのため、静的型チェック向けに実体のTextIOWrapperとしてcastする。castは実行時に何もしない。
    for stream in (sys.stdout, sys.stderr):
        cast(io.TextIOWrapper, stream).reconfigure(encoding='utf-8', errors='replace', newline='\n')


def _write(output):
    # ensure_ascii=False：日本語などを \uXXXX にせず、そのまま出力する。出力先はconfigure_output_streamsでUTF-8にする。
    # separators=(',', ':')：項目間やキーと値の間に余分な空白を入れない。
    # 引用符・バックスラッシュ・改行など、JSONとして必要なエスケープは、ensure_asciiの指定によらずjson.dumpsが常に行う。
    print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))


def write_deny_decision(reason):
    # preToolUse：ツール呼び出しを拒否し、reasonをエージェントに伝える。
    _write({'permissionDecision': 'deny', 'permissionDecisionReason': reason})


def write_block_decision(reason):
    # agentStop：応答の終了を差し戻し、reasonを次の指示としてエージェントに渡す。
    _write({'decision': 'block', 'reason': reason})


def write_additional_context(context):
    # sessionStart・userPromptSubmitted・postToolUse・postToolUseFailure：contextをエージェントへの助言として渡す。
    _write({'additionalContext': context})
