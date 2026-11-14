# record-seen-files hook README

## 発動条件

postToolUse（ツール `view`・`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` が成功した直後）

## 処理概要

閲覧・編集に成功したファイルを、このセッションで内容を把握したファイルとして記録する。他のhookが使う記録で、単独では何も出力しない。

## 処理詳細

1. ツール引数の `path`（`create` では `file_path`。引数名は [edit-tool-changes](../../../utilities/edit-tool-changes/README.md) と共有する）、または `apply_patch` のパッチ本文の `*** Add File:`・`*** Update File:`・`*** Move to:` の行から、対象のファイルを特定する。
2. 存在するファイルを、このセッションで内容を把握したファイルとして記録する。

## 例外

- ディレクトリの一覧表示、削除したファイル、移動元は記録しない。

## 制約と限界

- サブエージェントの閲覧・編集も親と同じ記録に含まれる。サブエージェントのツール呼び出しでもpostToolUseは発火するが、入力のセッションIDは親と同じで、サブエージェントを識別する項目もないため区別できない。影響は [require-read-before-edit](../../preToolUse/require-read-before-edit/README.md) の制約と限界を参照。

## 備考

- 次のhookが使う記録。単独では何も出力しない。
  - [require-read-before-edit](../../preToolUse/require-read-before-edit/README.md)：読まずに編集しようとしたかを判定する。
  - [detect-unread-speculation](../../agentStop/detect-unread-speculation/README.md)：最終応答で読んでいないファイルの中身を推測で語ったかを判定する。
- 閲覧に加えて編集の成功も記録する。置換やパッチの適用には現在の内容との一致が必要で、作成したファイルは内容を自分で書いているため。
- 状態は [known-files-state.py](../../../utilities/known-files-state/known-files-state.py) でrequire-read-before-edit hook・detect-unread-speculation hookと共有し、ファイルパスは保存せず照合用のSHA-256だけを保存する。
