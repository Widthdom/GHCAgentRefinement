# ツール名が、ファイルを編集するツールか、シェルを実行するツールかを判定する。
# ツールの種類で処理を分けるhookとutilityが共有する。

EDIT_TOOL_NAMES = frozenset({'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'})
SHELL_TOOL_NAMES = frozenset({'bash', 'powershell'})


def is_edit_tool(tool_name):
    # tool_name：`functions.` 接頭辞の有無を問わない。
    return tool_name.removeprefix('functions.') in EDIT_TOOL_NAMES


def is_shell_tool(tool_name):
    # tool_name：`functions.` 接頭辞の有無を問わない。
    return tool_name.removeprefix('functions.') in SHELL_TOOL_NAMES
