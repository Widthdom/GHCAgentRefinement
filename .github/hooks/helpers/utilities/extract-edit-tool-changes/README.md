# extract-edit-tool-changes utility README

## 役割
ファイル編集ツールの呼び出し1件から、ファイルごとの変更を取り出す。書き込む内容を検査するpreToolUse hook、編集後のファイルを確かめるpostToolUse hook、依頼中の編集を振り返るagentStop hookが共有する。

## 解析の規則
対象は `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`（CLI形式の `functions.` 接頭辞も許容する）。それ以外のツールからは何も取り出さない。JSON文字列に包まれた引数は、解析してから使う。

|ツール|対象のファイル|書き込む内容|置換前の内容|
|--|--|--|--|
|`edit`・`create`・`insert`・`str_replace_editor`|`path`|`path`・`command`・`old_str`（`old_string`）を除く引数の文字列|`old_str`（`old_string`）|
|`apply_patch`|`*** Add File:`・`*** Update File:` ごと|パッチの追加行（`+`）|パッチの削除行（`-`）|

- `str_replace_editor` の `view`・`undo_edit` は書き込みではないため対象外とする。
- 作成（`create`、`str_replace_editor` の `create`）では、ファイル全体の内容も取り出す。置換前と置換後の内容がそろう置換では、その組も取り出す。
- `apply_patch` では、文脈行（` `）を含む本文の行と、`*** Move to:` の移動先も取り出す。`*** Delete File:` は、呼び出し元が指定したときだけ対象にする。
- 引数名（`REMOVED_TEXT_KEYS`・`NEW_TEXT_KEYS`・`FULL_CONTENT_KEYS`）、書き込みではないコマンド（`NON_WRITE_COMMANDS`）、パッチの見出しの正規表現（`PATCH_FILE_PATTERN`・`PATCH_MOVE_PATTERN`）は、編集を再現する [simulate-edit-tool-changes](../simulate-edit-tool-changes/README.md) と共有する。変更する場合は両方の結果に反映される。
