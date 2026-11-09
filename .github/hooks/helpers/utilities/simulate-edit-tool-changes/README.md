# simulate-edit-tool-changes utility README

## 役割
ファイル編集ツールの呼び出し1件をツールの実行前に再現し、書き込み先のファイルごとに編集前と編集後のファイル全体の内容を求める。編集後のファイル全体を検査するhookが共有する。書き込む内容と置換前の内容（差分の断片）だけが必要なhookは [extract-edit-tool-changes](../extract-edit-tool-changes/README.md) を使う。

## 解析の規則
- `create`・`str_replace_editor` の `create`：`file_text`（`content`）を編集後の内容とする。
- `edit`・`str_replace_editor` の `str_replace`：現在の内容の最初の `old_str` を `new_str` に置き換える。
- `insert`・`str_replace_editor` の `insert`：現在の内容の `insert_line` 行目の後（`0` は先頭）に `new_str` を挿入する。
- `apply_patch`：`*** Add File:` は追加行（`+`）を編集後の内容とする。`*** Update File:` は現在の内容に `@@` で区切ったハンクを順に適用し、`*** Move to:` があれば移動先を書き込み先、移動元の内容を編集前の内容とする。`*** Delete File:` は対象外とする。
- `str_replace_editor` の `view`・`undo_edit` は書き込みではないため対象外とする。
- 編集前の内容は、まだないファイルでは `None` とする。
- 編集後の内容を特定できない編集（`old_str` やパッチの文脈行が見つからない、挿入行が範囲外、現在の内容がUTF-8でないなど）は、ツール自体が失敗するか判定できないため結果に含めない。

## 制約と限界
- 置換前の内容は改行コードを変換せずに照合するため、CRLFのファイルに `\n` で書いた `old_str` は見つからず、結果に含めない。
- パッチの適用は文脈行の照合（末尾の空白の違いは許容）による簡易的なもので、適用位置がツールと異なる場合がある。

## 利用するhook
- [block-exclusion-escapes](../../events/preToolUse/block-exclusion-escapes/README.md)
- [block-py314-incompatible-syntax](../../-repo-events/preToolUse/block-py314-incompatible-syntax/README.md)
