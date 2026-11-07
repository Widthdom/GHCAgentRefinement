# require-read-before-edit hook README

## 発動条件

preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要

このセッションでまだ読んでいない既存ファイルを編集しようとすると拒否し、先に読んで現在の内容に基づいて編集し直すよう求める。

## 処理詳細

1. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から既存の内容を書き換えるファイルを特定する。
   - `edit`・`insert`・`str_replace_editor`・`create`：ツール引数の `path`（`str_replace_editor` の `view` は除く）
   - `apply_patch`：パッチ本文の `*** Update File:` の行
2. 存在するファイルのうち、[record-seen-files](../../postToolUse/record-seen-files/README.md) hookがこのセッションで閲覧・編集を記録していないものを探す。
3. 該当するファイルがあれば、先に該当箇所を読んで現在の内容に基づいて編集し直すよう求める英語の理由とともに、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 前提条件

- [record-seen-files](../../postToolUse/record-seen-files/README.md) hookの記録で既読を判定する。状態は [known-files-state.py](../../../utilities/known-files-state/known-files-state.py) でrecord-seen-files hook・[detect-unread-speculation](../../agentStop/detect-unread-speculation/README.md) hookと共有する。

## 例外

- 次のファイルは内容を把握する必要がないため対象外とする。
  - 新規作成するファイル（`create` の対象が存在しない場合、`apply_patch` の `Add File`）
  - 削除するファイルと移動先（`apply_patch` の `Delete File`・`Move to`）
  - 存在しないファイル（編集自体がツール側で失敗する）

## 制約と限界

- 拒否は1ファイルにつき1回だけ。読めない事情がある場合でも、2回目の編集は許可して作業を止めない。
- hookはツール呼び出しごとに起動し、呼び出しごとに許可・拒否を返す。`apply_patch` で複数のファイルを編集する場合は、未読のファイルが1つでもあれば呼び出し全体を拒否する。
- 閲覧の記録は `view` のpostToolUseで行うため、同時に呼び出した `view` と編集では、編集の判定時に閲覧が記録済みとは限らない。
- bashの `cat` などで読んだ場合や、bashで作成・移動したファイルは記録されないため、最初の編集で1回拒否される。
- サブエージェントと親の既読は区別しない。サブエージェントのツール呼び出しでもpreToolUse・postToolUseは発火するが、入力のセッションIDは親と同じで、サブエージェントを識別する項目もないため。
  - サブエージェントが閲覧したファイルは、親が内容を直接見ていなくても既読とみなし、親の編集を許可する（判定が甘くなる）。
  - 逆に、親が閲覧したファイルはサブエージェントでも既読とみなす。サブエージェントの編集も同じ判定を受ける。

## 備考

- 記憶や推測だけで置換やパッチを作り、置換に失敗したり周囲のコードを壊したりするのを、失敗する前に防ぐためのhook。失敗の後に働く [detect-repeated-failures](../../postToolUseFailure/detect-repeated-failures/README.md) とは役割が分かれる。
- hookの異常終了（不正な入力や状態ファイルの破損）は、Copilotが編集の拒否として扱う。
- 状態のファイルパスは保存せず、照合用のSHA-256だけを保存する。
