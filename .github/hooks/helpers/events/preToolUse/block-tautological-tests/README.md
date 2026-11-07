# block-tautological-tests hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
テストファイルへ、値をそれ自身と比べるアサーションや、テスト対象そのものを差し替えるモックを新たに書き込む編集を1回拒否する。

## 処理詳細
1. [block-test-weakening](../block-test-weakening/README.md) hookの [test-weakening-definitions.json](../block-test-weakening/test-weakening-definitions.json) から、テストファイルとみなすパスの正規表現（`testFilePatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から書き込む内容と対象のファイルを特定する。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path`・`command`・置換前の内容（`old_str`）を除く引数の文字列を書き込む内容とする（`str_replace_editor` の `view`・`undo_edit` は除く）。
   - `apply_patch`：パッチの追加行（`+`）を書き込む内容、削除行（`-`）を置換前の内容とし、`*** Add File:`・`*** Update File:` の行から対象を特定する。
   - 各ツールの引数は [extract-edit-tool-changes.py](../../../utilities/extract-edit-tool-changes/extract-edit-tool-changes.py) で解析する。
3. テストファイルでなければ何もしない。
4. 書き込む内容から2つの値を比べるアサーションを取り出し、比べる2つの式が空白を除いて同じなら自己比較とみなす。変数は、置換前の内容・ファイルの現在の内容・書き込む内容で1回だけ代入されたものに限り、代入された式に置き換えて比べる（`expected = f(x)` の後の `assert f(x) == expected` など）。
5. Pythonのテスト関数 `test_<名前>...` の中（デコレーターを含む）で、`<名前>` 自身を差し替えるモックを、テスト対象の差し替えとみなす（`test_parse_empty` での `patch("app.parse")` など）。
6. 既存の内容（置換前の内容、ファイルの現在の内容）に同じアサーション・同じ行があれば、元からあるものとみなす。
7. いずれかに該当し、同じファイルの同じ種類（自己比較・テスト対象の差し替え）をまだ拒否していなければ、期待値を独立に求め、依存だけをモックするよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 検出する内容
|種類|例|
|--|--|
|2引数のアサーション|`Assert.Equal`・`Assert.AreEqual`・`Assert.StrictEqual`・`Assert.Same`・`assertEquals`・`self.assertEqual`・`self.assertIs`・`assert.deepStrictEqual`・`assert_eq!` の最初の2引数|
|制約・matcherのアサーション|`Assert.That(a, Is.EqualTo(b))`・`expect(a).toBe(b)`・`expect(a).toEqual(b)`・`a.Should().Be(b)`|
|Pythonの `assert` 文|`assert a == b`・`assert a is b`（`, "message"` は除いて比べる）|
|同じ呼び出しで求めた期待値|`var expected = calc.Add(1, 2); Assert.Equal(expected, calc.Add(1, 2));`|
|テスト対象の差し替え|`test_parse*` での `patch("app.parse")`・`mocker.patch.object(Parser, "parse")`・`monkeypatch.setattr("app.parse", ...)`|

## 前提条件
- Python 3.14以降の標準の `sqlite3` を使うため、SQLite自体のインストールは不要（公式Windows版Pythonにも同梱）。

## 例外
- 標準ライブラリ（`builtins`・`os`・`sys`・`time`・`datetime`・`random`・`subprocess`・`socket`・`shutil`・`io`）と組み込み関数の差し替えは、依存の置き換えとして対象外とする。
- 3文字未満の名前の差し替えは、テスト名との偶然の一致とみなして対象外とする。
- 再代入される変数は、どの値を指すか決まらないため置き換えない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じファイルの同じ種類につき1回だけ。反射律の `Equals` や決定性（同じ入力で同じ結果）のテストなど、意図した自己比較は同じ編集を再実行すれば通る。
- 式を文字列として比べるため、意味が同じでも書き方の違う式（`a + b` と `b + a`、別名の変数）は見逃す。
- テスト対象の差し替えは、Pythonで、テスト関数の `def` 行が同じ編集に含まれる場合だけ判定する。C#のMoqやJavaScriptの `jest.spyOn` は、テスト名とテスト対象の対応が決まらないため対象外とする。

## 備考
- テストが失敗したとき、エージェントが期待値をテスト対象自身に計算させたり、テスト対象をモックで置き換えたりして、実装によらず成功するテストにする事故を、書き込む前に防ぐためのhook。常に成功するリテラルのアサーション（`Assert.True(true)` など）は [block-test-weakening](../block-test-weakening/README.md) hookが扱い、本hookは見た目は検証していても実際には何も検証しないテストを扱う。
- 状態にはファイルパスを保存せず、照合用のSHA-256と拒否の種類だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-tautological-tests/<セッションID>.sqlite3` に保存する。拒否しない編集では状態ファイルを作らない。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
