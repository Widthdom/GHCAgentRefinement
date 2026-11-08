# detect-unmeasured-performance-claims hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
最終応答が「高速化した」「3倍速くなった」「メモリを削減した」と性能の改善を断定しているのに、ベンチマーク・プロファイラー・時間計測のコマンドを実行していなければ、計測して数値を報告するか未計測と訂正するよう差し戻す。

## 処理詳細
1. [performance-claim-definitions.json](./performance-claim-definitions.json) から、性能の改善の主張（`claimPatterns`）、推測・未計測を示す表現（`hedgePatterns`）、計測とみなすコマンド（`measurementCommandPatterns`）の正規表現を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（[wait-for-agent-stop-event](../../../utilities/wait-for-agent-stop-event/README.md)）。
3. ユーザーの最新の依頼以降について、最終応答と、bash・powershellツールで実行したコマンドとその終了コードを集める。
4. 最終応答からコードブロックと引用（`>` で始まる行）を除き、文（`。`・`！`・`？`・英語の文末・改行で区切る）ごとに `claimPatterns` と照合する。`hedgePatterns` を含む文は除き、最初に一致した文を主張とする。
5. 主張があり、終了コード0で終わったコマンドのどれも `measurementCommandPatterns` に一致しなければ、主張の文を示し、計測して数値を報告するか、未計測の見込みとして書き直すよう求める英語の理由とともに `{"decision":"block","reason":"..."}` を標準出力して差し戻す。

## 検出する内容

|種類|例|
|--|--|
|性能の改善の主張|`高速化した`・`速くなった`・`3倍高速`・`処理時間を短縮した`・`メモリ使用量を削減した`・`パフォーマンスが向上した`・`ボトルネックを解消した`・`3x faster`・`is now faster`・`speeds up`・`improves performance`・`reduced memory usage` など|
|推測・未計測（対象外にする）|`はず`・`見込み`・`可能性`・`かもしれない`・`期待`・`想定`・`理論上`・`未計測`・`計測していない`・`should`・`might`・`expected to`・`likely`・`not measured`・`isn't` など|
|計測とみなすコマンド|`hyperfine`・BenchmarkDotNet などの `bench` を含むコマンド・`go test -bench`・`cargo bench`・`pytest --benchmark-only`・`timeit`・`cProfile`・`py-spy`・`memray`・`tracemalloc`・`valgrind`・`dotnet-counters`・`dotnet-trace`・`perf stat`・`k6`・`wrk`・`time <コマンド>`・`Measure-Command`・`perf_counter`・`Stopwatch`・`performance.now` など|

## 例外
- サブエージェントの停止は対象外。
- 依頼の区切りは [get-turn-events](../../../utilities/get-turn-events/README.md) の規則に従う。
- 計算量の主張（`O(n^2)からO(n)にした`）は対象外とする。コードを読めば確かめられ、実測とは別の主張のため。
- 直後に否定が続く日本語の主張（`短縮されない`）は主張とみなさない。
- 終了コード0で終わらなかった計測コマンドや、拒否などで実行されなかったコマンドは計測とみなさない。

## 制約と限界
- 同じ依頼で差し戻すのは1回だけ。計測できない場合でも停止できるよう、2回目以降の応答は検査しない。
- 最新の依頼以降の計測だけを根拠とする。前の依頼で計測した結果を引用する場合も差し戻す。
- 計測の結果が主張と合っているか（本当に速くなったか、変更前と比べたか）は見ない。
- 主張・推測・計測コマンドは正規表現で推定するため、言い回しによっては検知漏れや誤検知がありうる。

## 背景
性能の改善は、読んだだけでは分からず、計測しなければ逆に遅くなっていることも誤差のこともある。エージェントはキャッシュや並列化を加えただけで「高速化した」と報告しがちで、[verify-test-pass-claims](../verify-test-pass-claims/README.md) hookはテストと動作確認の主張しか確かめない。性能の主張にも、実行したコマンドという根拠を求める。

## 備考
- 状態ファイルは作らない。差し戻し済みかはトランスクリプトの差し戻しメッセージで判定する。
- 終了コードはトランスクリプトの `shellExecution.exitCode` から取得する。
- 正規表現はPythonの `re` 構文で書き、大文字小文字を区別しない場合は `(?i)` を先頭に付ける。テスト用に `--definitions-path` で定義ファイルを差し替えられる。
