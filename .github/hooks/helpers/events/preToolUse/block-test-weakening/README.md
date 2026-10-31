# block-test-weakening hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理詳細
1. [test-weakening-definitions.json](./test-weakening-definitions.json) から、テストファイルのパス（`testFilePatterns`）、アサーション（`assertionPatterns`）、テストを弱める行（`weakeningPatterns`）の正規表現を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 並列に呼び出すツール（`toolCalls`）のうち編集系ツールから、対象のファイルごとに書き込む内容と置換前の内容を特定する。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path`・`command`・置換前の内容（`old_str`）を除く引数の文字列（`str_replace_editor` の `view` は除く）
   - `apply_patch`：`*** Add File:`・`*** Update File:` ごとのパッチの追加行（`+`）と削除行（`-`）、`*** Delete File:` のファイル
3. cwdからの相対パス（`/` 区切り）が `testFilePatterns` のいずれかに一致するファイルについて、次のいずれかに該当すれば拒否の候補とする。
   - 骨抜き：書き込む内容に、`weakeningPatterns` に一致する行がある。ただし、同じ行が既存の内容（置換前の内容、パッチの削除行、ファイルの現在の内容）にあれば除く。
   - アサーションの減少：`assertionPatterns` に一致する行の数が、置換前の内容より書き込む内容で少ない。`create` での上書きはファイルの現在の内容と、削除は現在の内容と空の内容とを比べる。
4. 候補のうち、このセッションで同じファイル・同じ種類をまだ拒否していないものがあれば、テストではなく実装を直すよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 検出する内容

|種類|例|
|--|--|
|テストファイル|`test_*.py`・`*_test.py`・`*_test.go`・`*Tests.cs`・`*Test.java`・`*.test.ts`・`*.spec.js`・`test-*.py`・`tests/`・`__tests__/`・`spec/`・`*.Tests/` 配下|
|スキップの属性・デコレーター|`[Fact(Skip = "...")]`・`[Ignore]`・`@Disabled`・`#[ignore]`・`@pytest.mark.skip`・`@pytest.mark.xfail`・`@unittest.skipIf(...)`|
|スキップの呼び出し|`pytest.skip(...)`・`self.skipTest(...)`・`t.Skip(...)`・`Assert.Inconclusive(...)`|
|JavaScriptのスキップ・限定実行|`it.skip(`・`describe.only(`・`test.todo(`・`xit(`・`fdescribe(`|
|常に成功するアサーション|`Assert.True(true)`・`Assert.Pass()`・`assert True`・`self.assertTrue(True)`・`expect(true).toBe(true)`|
|アサーション（数える対象）|`Assert.*(`・`CollectionAssert.*(`・`.Should()`・`assert*(`・`assert` 文・`assert_eq!(`・`expect(`・`t.Error(`・`t.Fatal(`・`pytest.raises(`|

## 備考
- 特別なプロンプト指定は不要。
- テストが失敗したとき、エージェントが実装を直さずに、テストをスキップしたり、アサーションを消したり、常に成功するアサーションに置き換えたりして成功させる事故を、書き込む前に防ぐためのhook。テストの成功の主張を確かめる [verify-completion-claims](../../agentStop/verify-completion-claims/README.md) hookと組み合わせ、テストを弱めて成功させる抜け道を塞ぐ。
- 限定実行（`.only` など）は、他のテストを実行させなくするため、スキップと同じく骨抜きとして扱う。
- 拒否は同じファイルの同じ種類（骨抜き・アサーションの減少）につき1回だけ。ユーザーの依頼による削除や、アサーションの移動・統合などの意図した編集でも、同じ編集を再実行すれば通るようにする。
- アサーションは行単位で数え、1行で複数のパターンに一致しても1件とする。ファイル間や複数回の編集をまたぐアサーションの移動は、それぞれの編集で判定する。
- 新規のテストファイルでも、スキップや限定実行の行は骨抜きとして扱う。
- 状態にはファイルパスを保存せず、照合用のSHA-256と拒否の種類だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-test-weakening/<セッションID>.sqlite3` に保存する。拒否しない編集では状態ファイルを作らない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。
- 正規表現で検出するため、定義にない形式（条件分岐による早期の `return` など）は見逃す。期待値をテスト対象の出力に合わせる書き換えは [block-goalpost-moving](../block-goalpost-moving/README.md) hookが拒否する。
- `testFilePatterns` はblock-goalpost-moving hookもテストファイルの判定に使う。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
- Python 3.14以降の標準の `sqlite3` を使うため、SQLite自体のインストールは不要（公式Windows版Pythonにも同梱）。
