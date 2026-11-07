# block-time-bomb-literals hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
テストファイルに今日に近い日付をリテラルで書き、実際の現在時刻と比べる編集を拒否し、現在時刻から日付を求めるか時計を固定するよう求める。

## 処理詳細
1. [time-bomb-definitions.json](./time-bomb-definitions.json) から、対象とする日付の範囲（今日の `pastDays` 日前から `futureDays` 日後まで）、現在時刻の参照の正規表現（`clockPatterns`）、時計の固定の正規表現（`frozenClockPatterns`）を読み込む。テストファイルの判定には [block-test-weakening](../block-test-weakening/README.md) hookの [test-weakening-definitions.json](../block-test-weakening/test-weakening-definitions.json) の `testFilePatterns` を使う。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から対象のファイルごとに書き込む内容と置換前の内容を特定する。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path`・`command`・置換前の内容（`old_str`）を除く引数の文字列（`str_replace_editor` の `view`・`undo_edit` は除く）。`create` での上書きはファイルの現在の内容と比べる。
   - `apply_patch`：`*** Add File:`・`*** Update File:` ごとのパッチの追加行（`+`）と削除行（`-`）。`*** Delete File:` は対象外とする。
   - 各ツールの引数は [extract-edit-tool-changes.py](../../../utilities/extract-edit-tool-changes/extract-edit-tool-changes.py) で解析する。
3. cwdからの相対パス（`/` 区切り）が `testFilePatterns` に一致するファイルについて、置換前の内容にない行（書き加えた行）から日付のリテラルを探す。
4. 書き加えた日付が範囲内で、編集後のファイル（現在の内容と書き込む内容）に `clockPatterns` があり、`frozenClockPatterns` がなければ拒否の候補とする。
5. 候補のうち、このセッションで同じファイルをまだ拒否していないものがあれば、現在時刻から日付を求めるか時計を固定するよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 検出する内容

|種類|例|
|--|--|
|文字列の日付|`"2026-11-03"`・`'2026/12/01'`|
|年・月・日からの生成|`new DateTime(2026, 11, 3)`・`date(2026, 11, 3)`・`LocalDate.of(2026, 11, 3)`・`Date.new(2026, 11, 3)`・`time.Date(2026, time.November, 3, ...)`・`NaiveDate::from_ymd_opt(2026, 11, 3)`|
|JavaScriptの0始まりの月|`new Date(2026, 10, 3)`・`Date.UTC(2026, 10, 3)`（11月3日として扱う）|
|現在時刻の参照|`DateTime.Now`・`DateTime.Today`・`TimeProvider.System`・`datetime.now()`・`date.today()`・`time.time()`・`Date.now()`・`new Date()`・`LocalDate.now()`・`time.Now()`・`Utc::now()`・`Time.now`・`Get-Date` など|
|時計の固定（対象外にする）|`freeze_time`・`freezegun`・`time_machine`・`FakeTimeProvider`・`FakeClock`・`useFakeTimers`・`setSystemTime`・`Clock.fixed`・`Timecop`・`patch("...datetime...")` など|

例：今日が2026-11-03のとき、次の編集を拒否する。

```diff
-    pass
+    assert not is_expired(date(2026, 12, 1), today=date.today())
```

## 前提条件
- 今日の日付は、hookを実行する環境のローカル時刻で決める。
- Python 3.14以降の標準の `sqlite3` を使うため、SQLite自体のインストールは不要（公式Windows版Pythonにも同梱）。

## 例外
- 範囲外の日付（既定では昨日より前と1年より後）は対象外とする。遠い過去・未来との比較は結果が変わらないため。
- 現在時刻を参照しないテストや、時計を固定しているテストは対象外とする。
- 置換前の内容にもあった行、コメント行（`#`・`//`・`/*`・`*`・`--`・`<!--` で始まる行）、存在しない日付（`2026-02-30`）は対象外とする。
- テストファイル以外とシェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じファイルにつきセッションで1回だけ。絶対的な日付を意図している場合は、同じ編集を再実行すれば通る。
- 日付と現在時刻の参照が同じ比較にあるかは見ず、同じファイルにあれば対象とする。
- 日付と時計は正規表現で推定するため、書き方によっては検知漏れや誤検知がありうる。

## 背景
今日の日付やその少し先の日付を期待値や有効期限として書いたテストは、書いた日には通るが、その日を過ぎると実装を変えなくても失敗する（時限爆弾）。エージェントは今日の日付を知っているため、期待値を今日の値で埋めがちになる。書く時点で止め、日付の求め方を直させる。

## 備考
- 状態にはパスを保存せず、照合用のSHA-256だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-time-bomb-literals/<セッションID>.sqlite3` に保存する。拒否しない編集では状態ファイルを作らない。
- 正規表現はPythonの `re` 構文で書き、大文字小文字を区別しない場合は `(?i)` を先頭に付ける。テスト用に `--definitions-path`・`--test-file-definitions-path` で定義ファイルを、`--today` で今日の日付を差し替えられる。
- 不正な入力では異常終了する。Copilotはhookの異常終了をツール実行の拒否として扱う。
