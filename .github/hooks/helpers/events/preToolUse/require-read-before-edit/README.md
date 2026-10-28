# require-read-before-edit hook README

## 発動条件

preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理詳細

1. 並列に呼び出すツール（`toolCalls`）のうち編集系ツールから、既存の内容を書き換えるファイルを特定する。
   - `edit`・`insert`・`str_replace_editor`・`create`：ツール引数の `path`（`str_replace_editor` の `view` は除く）
   - `apply_patch`：パッチ本文の `*** Update File:` の行
2. 存在するファイルのうち、[record-seen-files](../../postToolUse/record-seen-files/README.md) hookがこのセッションで閲覧・編集を記録していないものを探す。
3. 該当するファイルがあれば、先に該当箇所を読んで現在の内容に基づいて編集し直すよう求める英語の理由とともに、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 備考

- 特別なプロンプト指定は不要。
- 記憶や推測だけで置換やパッチを作り、置換に失敗したり周囲のコードを壊したりするのを、失敗する前に防ぐためのhook。失敗の後に働く [detect-repeated-failures](../../postToolUseFailure/detect-repeated-failures/README.md) とは役割が分かれる。
- 拒否は1ファイルにつき1回だけ。読めない事情がある場合でも、2回目の編集は許可して作業を止めない。
- 次のファイルは内容を把握する必要がないため対象外とする。
  - 新規作成するファイル（`create` の対象が存在しない場合、`apply_patch` の `Add File`）
  - 削除するファイルと移動先（`apply_patch` の `Delete File`・`Move to`）
  - 存在しないファイル（編集自体がツール側で失敗する）
- 拒否はツール呼び出しの単位ではなくhookの判断として返るため、同時に呼び出した編集系ツールはまとめて拒否される。編集系以外のツール（`view` など）は実行される。
- 同時に呼び出した `view` と編集は並列に実行され、編集は閲覧の結果を使えないため、閲覧済みとはみなさない。
- bashの `cat` などで読んだ場合や、bashで作成・移動したファイルは記録されないため、最初の編集で1回拒否される。
- hookの異常終了（不正な入力や状態ファイルの破損）は、Copilotが編集の拒否として扱う。
- 状態は [known-files-state.py](../../../utilities/known-files-state.py) でrecord-seen-files hookと共有し、ファイルパスは保存せず照合用のSHA-256だけを保存する。
- Python 3.14以降の標準の `sqlite3` を使うため、SQLite自体のインストールは不要（公式Windows版Pythonにも同梱）。
