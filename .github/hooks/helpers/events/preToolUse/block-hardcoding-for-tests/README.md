# block-hardcoding-for-tests hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
テストが失敗した後、特定の入力に対してだけテストの期待値を返す分岐を実装に加える編集を拒否し、一般的な実装を求める。

## 処理詳細
1. [check-test-files](../../../utilities/check-test-files/README.md) utilityで、テストファイルの判定の定義を読み込む。定義のファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. このセッションで [record-test-failure-values](../../postToolUse/record-test-failure-values/README.md) hookが記録した、最新のテスト実行の失敗の期待値を読み込む。記録がなければ許可する。
3. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から対象のファイルごとに書き込む行と、そのうちの追加行を特定する。
   - 置換前の内容にない行を追加行とする。`create` での上書きは、ファイルの現在の内容にない行を追加行とする。
   - `apply_patch` では、パッチの追加行（`+`）と文脈行（` `）を書き込む行とし、追加行だけを追加行とする。
   - 各ツールの引数は [edit-tool-changes](../../../utilities/edit-tool-changes/README.md) の規則で解析する。
4. テストファイル（cwdからの相対パス（`/` 区切り）で判定）と文章のファイル（`.md`・`.markdown`・`.txt`・`.rst`・`.adoc`）を除き、追加行の条件分岐を調べる。
   - 条件分岐：`if`・`elif`・`elsif`・`else if`・`unless`・`when`・`case` で始まる条件、または三項演算子（`==`・`===`・`!=`・`!==` から `?` まで）。
   - 条件にリテラル（文字列・数値・真偽値・null）があり、処理（同じ行の `return`・`yield`・`=>`・`then`・`:`・`)` の後、または続く3行以内で次の分岐（`else`・`case` など）より前）に期待値と同じリテラルがあれば、拒否の候補とする。
5. 候補のうち、このセッションで同じファイルをまだ拒否していないものがあれば、特定の入力だけ期待値を返す分岐ではなく一般的な実装を求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 例

`Expected: 4` / `Received: -1` で失敗した後の次の編集を拒否する。

```diff
 def add(a, b):
+    if a == 1 and b == 2:
+        return 4
     return a - b
```

次の編集は拒否しない。

- `return a - b` を `return a + b` に直す（分岐がない）
- `if a > b:` のように、条件にリテラルがない分岐を追加する
- 分岐の処理が期待値と異なる値を返す
- テストファイルや文章のファイルを編集する

## 前提条件
- [record-test-failure-values](../../postToolUse/record-test-failure-values/README.md) hookが記録したテストの失敗を使う。記録がなければ何も拒否しない。

## 例外
- テストファイルと文章のファイルは対象外とする。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じファイルにつき1回だけ。再帰の基底条件や仕様で決まった特別な値のように、分岐が正当な場合は同じ編集を再実行すれば通るようにする。
- 引用符の種類は比較しない（`"hi"` と `'hi'` は同じ値とみなす）。数値の表記の違い（`4` と `4.0`）は別の値とみなす。
- unittest・pytest・Rustの出力は期待値の側が決まらないため、両方の値を期待値として扱う。
- 対応表（辞書・マップ）での特別扱い、Rustの `match` やKotlinの `when` の腕のようにキーワードのない分岐、期待値を変数や定数を経由して返す分岐は見逃す。

## 備考
- テストが失敗したとき、エージェントがテストの入力に対してだけ期待値を返す分岐を実装に加え、一般的な処理を直さずにテストを成功させる事故を、書き込む前に防ぐためのhook。テストを書き換えないため、[block-test-weakening](../block-test-weakening/README.md) hookと [block-goalpost-moving](../block-goalpost-moving/README.md) hookでは検出できない。
- テストファイルの判定は、テストファイルを扱う他のhookと同じ基準にするため、utilityの定義を共有する。
- 状態は [assertion-failure-values-state.py](../../../utilities/assertion-failure-values-state/assertion-failure-values-state.py) でrecord-test-failure-values hook・block-goalpost-moving hookと共有する。値とファイルパスは保存せず、照合用のSHA-256と拒否済みのファイルだけを保存する。拒否済みのファイルはblock-goalpost-moving hookとは別に記録する。テストの失敗が記録されていなければ状態ファイルを作らない。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
