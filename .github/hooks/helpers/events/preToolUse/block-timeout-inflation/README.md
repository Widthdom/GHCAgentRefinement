# block-timeout-inflation hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
タイムアウト・待ち時間・再試行回数の数値を増やすだけの編集を拒否し、待ち時間を延ばさずに原因を直すよう求める。

## 処理詳細
1. [timeout-inflation-definitions.json](./timeout-inflation-definitions.json) から、対象外のファイルのパス（`excludedFilePatterns`）と、待ち時間・再試行を表す語の正規表現（`timingPatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から対象のファイルごとに書き込む内容と置換前の内容を特定する。
   - `create` での上書きは、ファイルの現在の内容と比べる。
   - 各ツールの引数は [edit-tool-changes](../../../utilities/edit-tool-changes/README.md) の規則で解析する。
3. cwdからの相対パス（`/` 区切り）が `excludedFilePatterns` に一致しないファイルについて、置換前の内容と書き込む内容を行単位（前後の空白を除く）で比べ、書き換えた行を取り出す。空行とコメントだけの行（`//`・`#`・`/*`・`*`・`--`・`<!--` で始まる行）は除く。
4. 書き込む側の行がすべて次の条件を満たし、置換前の側の行と1対1で対応すれば、待ち時間を延ばすだけの編集とみなす。
   - `timingPatterns` のいずれかに一致する
   - 数値リテラル以外の部分（`ms` などの接尾辞を含む）が一致する置換前の行があり、同じ位置の数値が1つ以上増え、減ったものがない
5. 該当するファイルのうち、このセッションでまだ拒否していないものがあれば、書き換えの前後の行を最大3組示し、待ち時間を延ばさずに原因を直すよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 例

次の編集を拒否する。

```diff
-    await Task.Delay(100);
+    await Task.Delay(5000);
```

```diff
-    timeout-minutes: 10
+    timeout-minutes: 60
```

次の編集は拒否しない。

- 待ち時間を延ばすのと同時に、待機の条件やロジックを書き換える（`time.sleep(1)` を `wait_until_ready()` にするなど）
- 行を追加・削除する、または待ち時間と関係のない行も書き換える
- 数値を減らす、または増減が混在する（`retry(times=3, delay=2)` を `retry(times=5, delay=1)` にするなど）
- 単位が変わる（`5m` を `300s`、`FromSeconds(5)` を `FromMinutes(1)` にするなど）
- 新しいファイルの作成や、`insert` による行の追加

## 対象の語

|語|例|
|--|--|
|タイムアウト|`Timeout`・`timeout-minutes`・`time_out`・`setTimeout`|
|遅延・スリープ|`Task.Delay`・`Thread.Sleep`・`time.sleep`|
|待機|`waitFor`・`WaitAsync`・`WAIT_SECONDS`（`await` は対象外）|
|再試行|`retry`・`MAX_RETRIES`・`stop_after_attempt`|
|間隔・バックオフ・期限|`POLL_INTERVAL`・`backoff`・`deadline`|

## 例外
- ロジックの変更を伴う編集は、原因に手を入れた可能性があるため対象外とする。同じ行の末尾にコメントを加えた場合も、行の形が変わるため対象外になる。
- 文書のファイルは対象外とする。設定ファイル（YAML・JSONなど）は、CIのタイムアウトなどがあるため対象にする。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じファイルにつき1回だけ。ユーザーが依頼した場合や、処理に本当にその時間がかかる場合は、同じ編集を再実行すれば通るようにする。
- `limit=10` のように待ち時間と関係のない数値でも、同じ行に対象の語があれば対象になる。

## 備考
- 遅い・不安定な処理やテストを、待ち時間・再試行回数を延ばすだけで「直した」ことにする事故を、書き込む前に防ぐためのhook。競合状態、`await` の漏れ、誤った待機条件などの原因が残り、テストやCIが遅くなる。
- 状態にはファイルパスを保存せず、照合用のSHA-256だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-timeout-inflation/<セッションID>.sqlite3` に保存する。拒否しない編集では状態ファイルを作らない。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
