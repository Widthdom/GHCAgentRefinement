# record-test-failure-values hook README

## 発動条件

postToolUse（ツール `bash`・`powershell` が成功した直後）

## 処理概要

テストの実行結果から、失敗したアサーションの期待値と実際の値の組を読み取り、最新のテスト実行の結果として記録する。他のhookが使う記録で、単独では何も出力しない。

## 処理詳細

1. ツール引数の `command` がテストの実行（`pytest`・`npm test`・`test-*.py` など）でなければ何もしない。テストの実行かどうかは、[check-test-runs.py](../../../utilities/check-test-runs/check-test-runs.py) の基準で判定する。
2. ツールの結果（`toolResult.textResultForLlm`）から、失敗したアサーションの期待値と実際の値の組を読み取る。
3. 値を正規化し（前後の空白・末尾のカンマ・1組の引用符を除く）、組と期待値をこのセッションの最新のテスト実行の結果として記録する。前回の記録は消す。

## 読み取る形式

|形式|例|期待値|
|--|--|--|
|期待値の行と、直後3行以内の実際の値の行|`Expected: 4` / `Received: 3`（Jest）・`Actual:`（xUnit・testify）・`But was:`（NUnit・AssertJ）|`Expected` 側|
|JUnit|`expected: <4> but was: <3>`|`expected` 側|
|Go|`got 3, want 4`・`want 4, got 3`|`want` 側|
|unittest|`AssertionError: 3 != 4`|決まらない（両方の向きを記録）|
|pytest|`E       assert 3 == 4`|決まらない（両方の向きを記録）|
|Rust|`left: 3` / `right: 4`|決まらない（両方の向きを記録）|

## 例外

- 最新のテスト実行の結果だけを残す。成功した実行や、形式を読み取れない出力では前回の記録を消し、修正後の正当な編集を古い失敗で拒否しないようにする。

## 制約と限界

- 記録は1回の実行につき100組まで。空、複数行、200文字を超える値は記録しない。
- サブエージェントのテスト実行も親と同じ記録に含まれる。postToolUseの入力では親と区別できないため。

## 備考

- [block-goalpost-moving](../../preToolUse/block-goalpost-moving/README.md) hookと [block-hardcoding-for-tests](../../preToolUse/block-hardcoding-for-tests/README.md) hookが使う記録。単独では何も出力しない。
- テストの失敗はコマンドの非ゼロ終了で表され、ツール自体は成功扱いになるため、postToolUseFailureではなくpostToolUseで記録する。
- 状態は [assertion-failure-values-state.py](../../../utilities/assertion-failure-values-state/assertion-failure-values-state.py) でblock-goalpost-moving hook・block-hardcoding-for-tests hookと共有する。値そのものは保存せず、照合用のSHA-256だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/test-failure-values/<セッションID>.sqlite3` に保存する。読み取れる組がなく、消す記録もなければ状態ファイルを作らない。
