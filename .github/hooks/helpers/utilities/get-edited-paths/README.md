# get-edited-paths utility README

## 役割
ファイル編集ツールの呼び出し1件から、対象のファイルのパスを取り出す。編集したファイルを検査・記録するhookが共有し、パスの取り出し方を1か所で管理する。書き込む内容も必要なhookは [extract-edit-tool-changes](../extract-edit-tool-changes/README.md) を使う。

## 解析の規則
- `apply_patch`：パッチ本文の `*** Add File:`・`*** Update File:`・`*** Delete File:`・`*** Move to:` の行のパスを、出現順に取り出す。対象にする行の種類は、呼び出し元が指定する（既定はすべて）。
- それ以外の編集ツール：ツール引数の `path`（なければ `file_path`）を取り出す。`edit`・`view` は `path`、`create` は `file_path` でパスを渡す。`str_replace_editor` の `view` は閲覧なので対象外とする。
- パスの引数名（`PATH_KEYS`）と取り出し方（`get_path_argument`）は、[extract-edit-tool-changes](../extract-edit-tool-changes/README.md)・[simulate-edit-tool-changes](../simulate-edit-tool-changes/README.md)、[record-seen-files](../../events/postToolUse/record-seen-files/README.md)・[block-nonportable-paths](../../-repo-events/preToolUse/block-nonportable-paths/README.md) hookも使う。
- パスはツールに渡された表記のまま返す。cwdを基準にした解決や正規化は、呼び出し元が行う。

## 利用するhook
- [block-generated-file-edits](../../events/preToolUse/block-generated-file-edits/README.md)
- [block-hook-tampering](../../events/preToolUse/block-hook-tampering/README.md)
- [require-read-before-edit](../../events/preToolUse/require-read-before-edit/README.md)
- [check-cross-agent-skill-wrappers](../../events/postToolUse/check-cross-agent-skill-wrappers/README.md)
- [detect-edit-oscillation](../../events/postToolUse/detect-edit-oscillation/README.md)
- [preserve-line-endings](../../events/postToolUse/preserve-line-endings/README.md)
- [require-hook-tests](../../events/agentStop/require-hook-tests/README.md)
