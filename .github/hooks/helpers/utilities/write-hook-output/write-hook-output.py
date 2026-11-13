import json

# hookの判定や助言を、Copilot SDKが解釈するトップレベルのJSONとして標準出力に書く。
# 判定や助言を出力するhookが共有する。正常な介入なので、呼び出し元のhookは終了コード0で終える。
# 入出力仕様：https://docs.github.com/en/copilot/reference/hooks-reference


def _write(output):
    # ensure_ascii=False：日本語などを \uXXXX にせず、そのまま出力する。出力先は呼び出し元のhookがUTF-8に設定する。
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
