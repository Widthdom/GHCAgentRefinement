# record-seen-files hook README

## 発動条件

postToolUse（ツール `view`・`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` が成功した直後）

## 処理詳細

1. ツール引数の `path`、または `apply_patch` のパッチ本文の `*** Add File:`・`*** Update File:`・`*** Move to:` の行から、対象のファイルを特定する。
2. 存在するファイルを、このセッションで内容を把握したファイルとして記録する。

## 備考

- 特別なプロンプト指定は不要。
- [require-read-before-edit](../../preToolUse/require-read-before-edit/README.md) hookが、読まずに編集しようとしたかを判定するために使う記録。単独では何も出力しない。
- 閲覧に加えて編集の成功も記録する。置換やパッチの適用には現在の内容との一致が必要で、作成したファイルは内容を自分で書いているため。
- ディレクトリの一覧表示、削除したファイル、移動元は記録しない。
- サブエージェントの閲覧・編集も親と同じ記録に含まれる。サブエージェントのツール呼び出しでもpostToolUseは発火するが、入力のセッションIDは親と同じで、サブエージェントを識別する項目もないため区別できない。影響は [require-read-before-edit](../../preToolUse/require-read-before-edit/README.md) の備考を参照。
- 状態は [known-files-state.py](../../../utilities/known-files-state.py) でrequire-read-before-edit hookと共有し、ファイルパスは保存せず照合用のSHA-256だけを保存する。
