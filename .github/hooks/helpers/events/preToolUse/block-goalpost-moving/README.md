# block-goalpost-moving hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
テストが失敗した後、テストファイルの期待値を失敗時の実際の値に書き換える編集を拒否し、テストではなく実装を直すよう求める。

## 処理詳細
1. [block-test-weakening](../block-test-weakening/README.md) hookの [test-weakening-definitions.json](../block-test-weakening/test-weakening-definitions.json) から、テストファイルのパスの正規表現（`testFilePatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. このセッションで [record-test-failure-values](../../postToolUse/record-test-failure-values/README.md) hookが記録した、最新のテスト実行の失敗の期待値と実際の値の組を読み込む。記録がなければ許可する。
3. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から対象のファイルごとに書き込む内容と置換前の内容を特定する。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path`・`command`・置換前の内容（`old_str`）を除く引数の文字列（`str_replace_editor` の `view`・`undo_edit` は除く）。`create` での上書きはファイルの現在の内容と比べる。
   - `apply_patch`：`*** Add File:`・`*** Update File:` ごとのパッチの追加行（`+`）と削除行（`-`）。`*** Delete File:` は対象外とする。
   - 各ツールの引数は [extract-edit-tool-changes.py](../../../utilities/extract-edit-tool-changes/extract-edit-tool-changes.py) で解析する。
4. cwdからの相対パス（`/` 区切り）が `testFilePatterns` のいずれかに一致するファイルについて、置換前の行と書き込む行のうち、リテラル（文字列・数値・真偽値・null）以外の部分が一致する組を対応させる。同じ位置のリテラルが、記録した期待値から実際の値に変わっていれば拒否の候補とする。
5. 候補のうち、このセッションで同じファイルをまだ拒否していないものがあれば、テストではなく実装を直すよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 例

`E       assert 3 == 4` で失敗した後の次の編集を拒否する。

```diff
-    assert add(1, 2) == 4
+    assert add(1, 2) == 3
```

次の編集は拒否しない。

- 実装の `return 3` を `return 4` に直す（テストファイルではない）
- 期待値を記録にない値（`5` など）に変える
- 期待値の行を、リテラル以外の部分も含めて書き直す

## 前提条件
- [record-test-failure-values](../../postToolUse/record-test-failure-values/README.md) hookが記録したテストの失敗を使う。記録がなければ何も拒否しない。

## 例外
- テストファイルの中だけを対象にするため、実装の修正は拒否しない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じファイルにつき1回だけ。仕様の変更やユーザーの依頼で期待値自体が誤っていた場合でも、同じ編集を再実行すれば通るようにする。
- 引用符の種類は比較しない（`"hi"` と `'hi'` は同じ値とみなす）。数値の表記の違い（`4` と `4.0`）は別の値とみなす。
- unittest・pytest・Rustの出力は期待値の側が決まらないため、どちらの向きの書き換えも拒否の候補にする。
- 期待値を別の行の変数や定数に移すなど、行の形が変わる書き換えは見逃す。

## 備考
- テストが失敗したとき、エージェントが実装を直さずに、期待値をテスト対象の現在の出力に書き換えて成功させる事故を、書き込む前に防ぐためのhook。アサーションを消さずに済むため、[block-test-weakening](../block-test-weakening/README.md) hookでは検出できない。
- テストファイルの判定はblock-test-weakening hookと定義を共有し、二重管理しない。
- 実装に期待値を返す分岐を加えてテストを通す編集は、[block-hardcoding-for-tests](../block-hardcoding-for-tests/README.md) hookが拒否する。
- 状態は [assertion-failure-values-state.py](../../../utilities/assertion-failure-values-state/assertion-failure-values-state.py) で [record-test-failure-values hook](../../postToolUse/record-test-failure-values/README.md) ・ [block-hardcoding-for-tests hook](../../preToolUse/block-hardcoding-for-tests/README.md) と共有する。値とファイルパスは保存せず、照合用のSHA-256と拒否済みのファイルだけを保存する。テストの失敗が記録されていなければ状態ファイルを作らない。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
