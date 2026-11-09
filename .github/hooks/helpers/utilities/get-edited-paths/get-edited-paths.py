import os
import re
import runpy

# 編集ツールの呼び出し1件から、対象のファイルのパスを取り出す。
# 編集したファイルを検査・記録するpreToolUse hook、postToolUse hook、agentStop hookが共有する。

# 他のutilityの処理は、スクリプトから読み込む。utilityのフォルダーの親がutilitiesである。
_utilities_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_collect_json_string_values = runpy.run_path(os.path.join(
    _utilities_directory, 'collect-json-string-values', 'collect-json-string-values.py',
))['collect_json_string_values']

_ALL_PATCH_OPERATIONS = frozenset({'Add File', 'Update File', 'Delete File', 'Move to'})
_PATCH_PATH_PATTERN = re.compile(r'^\*\*\* (Add File|Update File|Delete File|Move to): (.+?)\s*$', re.MULTILINE)


def get_edited_paths(tool_name, tool_args, patch_operations=_ALL_PATCH_OPERATIONS):
    # tool_name：`functions.` 接頭辞を外したツール名。tool_args：JSON文字列の包みを外したツール引数。
    # patch_operations：apply_patchで対象にする行の種類（`*** Add File:` なら 'Add File'）。
    # 戻り値は、ツールに渡された表記のままのパスのリスト。apply_patch以外は `path` 引数で、str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'apply_patch':
        return [
            path for text in _collect_json_string_values(tool_args)
            for operation, path in _PATCH_PATH_PATTERN.findall(text) if operation in patch_operations
        ]
    if not isinstance(tool_args, dict):
        return []
    if tool_name == 'str_replace_editor' and tool_args.get('command') == 'view':
        return []
    path = tool_args.get('path')
    return [path] if isinstance(path, str) and path.strip() else []
