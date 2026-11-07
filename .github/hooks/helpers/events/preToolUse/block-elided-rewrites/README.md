# block-elided-rewrites hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
既存のファイルを `// ... existing code ...` のような省略コメントで書き換える編集や、上書きでファイルを大きく縮める編集を、元の内容が消える前に拒否する。

## 処理詳細
1. [elision-definitions.json](./elision-definitions.json) から、省略を表すプレースホルダーの正規表現を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から対象のファイルごとに書き込む内容を特定する。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path`・`command`・置換前の内容（`old_str`）を除く引数の文字列（`str_replace_editor` の `view`・`undo_edit` は除く）
   - `apply_patch`：`*** Add File:`・`*** Update File:` ごとのパッチの追加行（`+`）
   - 各ツールの引数は [extract-edit-tool-changes.py](../../../utilities/extract-edit-tool-changes/extract-edit-tool-changes.py) で解析する。
3. 既存のファイルについて、次のいずれかに該当すれば拒否の候補とする。
   - 省略：書き込む内容に、定義に一致する行がある。ただし、同じ行が既存の内容（置換前の内容、パッチの削除行、ファイルの現在の内容）にあれば除く。
   - 縮小：`create`（`str_replace_editor` の `create` を含む）での上書きで、現在20行以上あるファイルが半分未満の行数になる。
4. 候補のうち、このセッションで同じファイル・同じ種類をまだ拒否していないものがあれば、内容を省略せずに書くか、変える部分だけを置換するよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 検出するプレースホルダー
コメント記号（`//`・`#`・`/*`・`<!--`・`--`・`;`・`*`）で始まる行のうち、次のもの。

|種類|例|
|--|--|
|省略記号だけのコメント|`# ...`、`// …`、`<!-- ... -->`|
|英語の省略コメント|`// ... existing code ...`、`# rest of the file unchanged`、`// (remaining methods omitted)`、`/* omitted for brevity */`|
|日本語の省略コメント|`# 既存のコード`、`// ... 以下同じ ...`、`# 残りは変更なし`、`# (省略)`|

## 例外
- 新規ファイルは消える内容がないため、省略の判定から除く。骨組みとしてのプレースホルダーは許可する。
- `...` だけの行（Pythonの `Ellipsis` など）や、行の途中の `...` は対象外とする。コメント記号で始まり、省略の表現が行頭近くにある行だけを対象とする。
- `edit` で大きな範囲を小さく置換する編集は、意図した削除と区別できないため縮小として扱わない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じファイルの同じ種類（省略・縮小）につき1回だけ。ユーザーの依頼による意図的な削除や、プレースホルダーを説明する文書などの誤検知でも、同じ編集を再実行すれば通るようにする。

## 備考
- エージェントがファイルを書き直すとき、変えない部分を `// ... existing code ...` のような省略記号で済ませると、元の実装がそのコメントに置き換わって消える。この事故を書き込む前に防ぐためのhook。
- 状態にはファイルパスを保存せず、照合用のSHA-256と拒否の種類だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-elided-rewrites/<セッションID>.sqlite3` に保存する。拒否しない編集では状態ファイルを作らない。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
