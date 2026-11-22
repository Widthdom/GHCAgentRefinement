import json

# ツール呼び出しの名前と引数を解釈する。ツールの種類で処理を分け、引数を検査・記録するhookとutilityが共有する。
# ツール名はCLI形式の `functions.` 接頭辞が付く場合があり、引数はJSON文字列に包まれて渡される場合がある。

EDIT_TOOL_NAMES = frozenset({'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'})
SHELL_TOOL_NAMES = frozenset({'bash', 'powershell'})
_TOOL_NAME_PREFIX = 'functions.'


def get_tool_name(tool_name):
    # `functions.` 接頭辞を外したツール名を返す。
    return tool_name.removeprefix(_TOOL_NAME_PREFIX)


def is_edit_tool(tool_name):
    # tool_name：`functions.` 接頭辞の有無を問わない。
    return get_tool_name(tool_name) in EDIT_TOOL_NAMES


def is_shell_tool(tool_name):
    # tool_name：`functions.` 接頭辞の有無を問わない。
    return get_tool_name(tool_name) in SHELL_TOOL_NAMES


def unwrap_tool_args(raw_tool_args):
    # 引数がJSON文字列に包まれていれば、解析して中身を返す。
    # JSONとして解析できない文字列（apply_patchの生のパッチなど）と、文字列以外の値はそのまま返す。
    if isinstance(raw_tool_args, str):
        try:
            return json.loads(raw_tool_args)
        except ValueError:
            return raw_tool_args
    return raw_tool_args


def parse_tool_call(tool_name, raw_tool_args):
    # `functions.` 接頭辞を外したツール名と、包みを外した引数の組を返す。
    return get_tool_name(tool_name), unwrap_tool_args(raw_tool_args)
