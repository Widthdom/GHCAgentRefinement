# detect-vacuous-test-success hook README

## 発動条件

postToolUse（bash・powershellツールが成功した直後）

## 処理概要

テストの実行結果から件数を読み取り、1件も実行されなかった場合や、同じコマンドの前回より実行件数が減った・スキップが増えた場合に、原因を直すか意図を明示するよう助言する。

## 処理詳細

1. ツール引数の `command` がテストの実行（`pytest`・`npm test`・`test-*.py` など）でなければ何もしない。テストの実行かどうかは、[check-test-runs.py](../../../utilities/check-test-runs/check-test-runs.py) の基準で判定する。
2. ツールの結果（`toolResult.textResultForLlm`）から色付けのエスケープシーケンスを除き、テストの件数の集計行を読み取って、実行した件数とスキップした件数を合計する（[読み取る形式](#読み取る形式)を参照）。
3. 集計行がなければ、テストが1件も実行されなかったと分かる出力（`no tests ran`・`No tests found` など）を探し、あれば実行・スキップとも0件とする。どちらもなければ判断できないため、記録を変えずに終了する。
4. 同じコマンド（前後の空白を除いた文字列が一致するもの）について、このセッションで前回記録した件数を取り出し、今回の件数で置き換える。
5. 次の場合に、英語の助言を `additionalContext` として渡す。
   - 実行した件数が0件：絞り込みやパスの指定が何にも一致していない可能性があり、この実行では何も検証していないこと。指定を直して再実行し、テストが通ったと報告しないよう求める。同じコマンドで前回も0件だった場合は繰り返さない。
   - 前回より実行した件数が減った、またはスキップした件数が増えた：テストを削除・スキップ・除外して通したのなら元に戻して原因を直し、意図した変更なら最終応答で明示するよう求める。

## 読み取る形式

|ツール|集計行の例|実行した件数|スキップした件数|
|--|--|--|--|
|pytest|`=== 3 passed, 1 failed, 2 skipped in 0.12s ===`・`3 passed in 0.12s`|passed・failed・error・xfailed・xpassed|skipped・deselected|
|unittest|`Ran 4 tests in 0.001s` と `OK (skipped=1)`|Ran − skipped|skipped|
|Jest|`Tests:       1 failed, 1 skipped, 3 passed, 5 total`|total − skipped・todo|skipped・todo|
|Vitest|`Tests  3 passed \| 1 skipped (4)`|括弧内 − skipped・todo|skipped・todo|
|Mocha|`3 passing`・`1 failing`・`2 pending`|passing・failing|pending|
|RSpec|`5 examples, 1 failure, 2 pending`|examples − pending|pending|
|cargo test|`test result: ok. 3 passed; 0 failed; 1 ignored; ...`|passed・failed|ignored|
|dotnet test|`Passed!  - Failed: 0, Passed: 5, Skipped: 1, Total: 6`|Failed・Passed|Skipped|
|PHPUnit|`OK (3 tests, 5 assertions)`・`Tests: 4, Assertions: 6, Skipped: 1.`|Tests − Skipped|Skipped|
|Pester|`Tests Passed: 3, Failed: 0, Skipped: 1 NotRun: 0`|Passed・Failed|Skipped|
|go test|`ok  	example.com/pkg	0.01s`|テストを実行したパッケージの数|なし|

|0件と分かる出力|ツール|
|--|--|
|`no tests ran`・`collected 0 items`|pytest|
|`No tests found`|Jest|
|`No test files found`|Vitest|
|`No test matches the given testcase filter`・`No test is available in`|dotnet test|
|`testing: warning: no tests to run`|go test|
|`No tests executed`|PHPUnit|

## 前提条件

- 特別なプロンプト指定は不要。
- Python 3.14以降の標準の `sqlite3` を使うため、SQLite自体のインストールは不要（公式Windows版Pythonにも同梱）。

## 例外

- go testの `[no test files]` のパッケージは数えない。
- テストコマンドでない場合と、件数を判断できない出力では何もしない。

## 制約と限界

- 件数を比べるのは同じコマンドの間だけ。`-k` などの絞り込みを変えたコマンドとは比べないため、絞り込みで件数を減らしたことは検出できない。ただし、絞り込みが何にも一致しなかった場合は0件として検出する。
- 出力が途中で省略された場合は集計行が欠け、件数の減少として誤って通知することがある。助言なので処理は止めない。
- 削除の依頼などによる意図的な減少でも通知する。助言には、意図した変更なら最終応答で明示するよう書いている。
- サブエージェントのテスト実行も親と同じ記録に含まれる。postToolUseの入力では親と区別できないため。

## 備考

- 絞り込みの誤りでテストが1件も実行されないまま成功したり、スキップの追加やshellでの削除でテストが減ったりしたことに、実行の直後に気づかせるためのhook。[block-test-weakening](../../preToolUse/block-test-weakening/README.md) hookは編集ツールによる骨抜きしか検出できない。
- 1回のコマンドで複数のテストを実行した場合（`test-all-hooks.py`・`cargo test`・`dotnet test` など）は、集計行の件数を合計する。go testは件数を出さないため、テストを実行したパッケージの数を件数とする。
- テストの失敗はコマンドの非ゼロ終了で表され、ツール自体は成功扱いになるため、postToolUseで検査する。
- コマンドは保存せず、照合用のSHA-256と件数だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/test-counts/<セッションID>.sqlite3` に保存する。テストコマンドでない場合と、件数を判断できない出力では状態ファイルを作らない。
