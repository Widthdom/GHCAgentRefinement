import json

# ツール呼び出しの引数（toolArgs）の包みを外し、中の引数を取り出す。
# ツールの引数を検査・記録するpreToolUse hook、postToolUse hook、utilityが共有する。


def unwrap_tool_args(raw_tool_args):
    # toolArgsはJSONオブジェクトのほか、JSON文字列に包まれて渡される場合もある。包まれていれば解析して中身を返す。
    # JSONとして解析できない文字列（apply_patchの生のパッチなど）と、文字列以外の値はそのまま返す。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args
