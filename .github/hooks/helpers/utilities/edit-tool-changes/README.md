# edit-tool-changes utility README

## 役割
ファイル編集ツールの呼び出し1件を解釈する。編集したファイルを検査・記録するpreToolUse hook、postToolUse hook、依頼中の編集を振り返るagentStop hookが共有し、編集ツールの引数とパッチの書式の解釈を1か所で管理する。

|関数|取り出すもの|
|--|--|
|`get_edited_paths`|対象のファイルのパス|
|`extract_edit_tool_changes`|ファイルごとの変更（書き込む内容と置換前の内容）|
|`simulate_edit_tool_changes`|ツールの実行前に編集を再現した、ファイルごとの編集前と編集後のファイル全体の内容|

## 共通の解析の規則
対象は `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`（CLI形式の `functions.` 接頭辞も許容する）。それ以外のツールからは何も取り出さない。JSON文字列に包まれた引数は、解析してから使う。

- 対象のファイルは、ツール引数の `path`（なければ `file_path`）とする。`edit`・`view` は `path`、`create` は `file_path` でパスを渡す。引数名（`PATH_KEYS`）と取り出し方（`get_path_argument`）は、[record-seen-files](../../events/postToolUse/record-seen-files/README.md)・[block-nonportable-paths](../../-repo-events/preToolUse/block-nonportable-paths/README.md) hookも使う。
- `str_replace_editor` は `command` 引数、それ以外のツールはツール名で操作が決まる。`view` は閲覧、`undo_edit` は書き込む内容を持たないため、書き込みではない（`NON_WRITE_COMMANDS`）。
- 置換前の内容は `old_str`（`old_string`）、新しい内容は `new_str`（`new_string`）、作成するファイル全体の内容は `file_text`（`content`）から読む。
- `apply_patch` では、引数の文字列（入れ子を含む）にあるパッチを、`*** Add File:`・`*** Update File:`・`*** Delete File:` の見出しごとに分ける。`*** Move to:` は直前の見出しのファイルの移動先とし、`*** End Patch` 以降の行は見出しが現れるまで無視する。本文は `@@` でハンクに分け、空行は先頭の空白が落ちた文脈行（` `）として扱う。
- パスはツールに渡された表記のまま返す（`simulate_edit_tool_changes` を除く）。cwdを基準にした解決や正規化は、呼び出し元が行う。

## get_edited_paths
- `apply_patch`：パッチの `*** Add File:`・`*** Update File:`・`*** Delete File:`・`*** Move to:` の行のパスを、出現順に取り出す。対象にする行の種類は、呼び出し元が指定する（既定はすべて）。
- それ以外の編集ツール：対象のファイルのパスを取り出す。`view` は対象外とし、ファイルを書き換える `undo_edit` は対象とする。

## extract_edit_tool_changes

|ツール|書き込む内容|置換前の内容|
|--|--|--|
|`edit`・`create`・`insert`・`str_replace_editor`|`path`・`file_path`・`command`・`old_str`（`old_string`）を除く引数の文字列|`old_str`（`old_string`）|
|`apply_patch`|パッチの追加行（`+`）|パッチの削除行（`-`）|

- 書き込みではない操作（`view`・`undo_edit`）は対象外とする。
- 作成（`create`、`str_replace_editor` の `create`）では、ファイル全体の内容も取り出す。置換前と置換後の内容がそろう置換では、その組も取り出す。
- `apply_patch` では、文脈行（` `）を含む本文の行と、`*** Move to:` の移動先も取り出す。`*** Delete File:` は、呼び出し元が指定したときだけ対象にする。

## simulate_edit_tool_changes
- `create`・`str_replace_editor` の `create`：`file_text`（`content`）を編集後の内容とする。
- `edit`・`str_replace_editor` の `str_replace`：現在の内容の最初の `old_str`（`old_string`）を `new_str`（`new_string`）に置き換える。
- `insert`・`str_replace_editor` の `insert`：現在の内容の `insert_line` 行目の後（`0` は先頭）に `new_str`（`new_string`）を挿入する。
- `apply_patch`：`*** Add File:` は追加行（`+`）を編集後の内容とする。`*** Update File:` は現在の内容にハンクを順に適用し、`*** Move to:` があれば移動先を書き込み先、移動元の内容を編集前の内容とする。`*** Delete File:` は対象外とする。
- 書き込みではない操作（`view`・`undo_edit`）は対象外とする。
- 書き込み先はcwdを基準にした絶対パスで返す。編集前の内容は、まだないファイルでは `None` とする。
- 編集後の内容を特定できない編集（`old_str` やパッチの文脈行が見つからない、挿入行が範囲外、現在の内容がUTF-8でないなど）は、ツール自体が失敗するか判定できないため結果に含めない。

## 制約と限界
- `simulate_edit_tool_changes` は置換前の内容を改行コードを変換せずに照合するため、CRLFのファイルに `\n` で書いた `old_str` は見つからず、結果に含めない。
- `simulate_edit_tool_changes` のパッチの適用は文脈行の照合（末尾の空白の違いは許容）による簡易的なもので、適用位置がツールと異なる場合がある。
