# block-masked-exit-codes hook README

## 発動条件

preToolUse（bashツールの実行前）

## 処理概要

テスト・ビルド・静的検査の結果を `tail` などにパイプし、`pipefail` なしで失敗の終了コードが隠れるコマンドを拒否する。

## 処理詳細

1. 呼び出すツール（`toolName`・`toolArgs`）がbashなら、そのコマンドをパイプライン（`|`・`|&` でつないだコマンドの列）に分ける。`;`・`&&`・`||`・`&`・括弧・改行でパイプラインが終わる。
2. 最後の段以外にテスト・ビルド・静的検査のコマンドがあり、それより前で `set -o pipefail` が有効になっていないパイプラインを探す。
3. 該当するコマンドがあり、このセッションで同じコマンドをまだ拒否していなければ、`set -o pipefail;` を付けるか出力をファイルに書き出して別に確認するよう求める英語の理由とともに、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## テスト・ビルド・静的検査とみなすコマンド

- 段の語に `test`・`tests`・`pytest`・`unittest`・`jest`・`vitest`・`mocha`・`rspec`・`phpunit`・`ctest`・`tox`・`pester`・`build`・`msbuild`・`make`・`tsc`・`lint`・`eslint`・`ruff`・`mypy`・`clippy` を含むもの（`dotnet test`・`npm run build`・`python3 test-all-hooks.py` など）。テストの語は、[check-test-runs.py](../../../utilities/check-test-runs/check-test-runs.py) がテストの実行とみなす語と同じ。
- 先頭の環境変数の代入（`CI=1`）、`env`・`sudo`・`time`・`nohup`・`xargs`・`timeout <時間>` などのラッパーは読み飛ばし、その後のコマンドで判定する。
- `cat`・`grep`・`rg`・`find`・`ls`・`git`・`head`・`tail` などの読むだけのコマンドは、`grep test` のように語を含んでも対象外とする。

## 例外

- 次のコマンドは許可する。
  - 同じコマンド内で、パイプラインより前に `set -o pipefail`・`set -euo pipefail` などを実行しているもの（後の `set +o pipefail` で無効に戻る）
  - `PIPESTATUS` を参照して各段の終了コードを確かめているもの
  - テスト・ビルドがパイプラインの最後の段にあるもの（`cat input | pytest`）。終了コードがそのまま伝わるため。
  - 引用符の中、コメント、ヒアドキュメントの本文に書かれた `|`
- powershellは対象外とする。ネイティブコマンドの終了コードは `$LASTEXITCODE` に残り、パイプラインの最後のコマンドで上書きされるとは限らないため。

## 制約と限界

- 拒否は同じコマンドにつきセッションで1回だけ。前のツール呼び出しで `set -o pipefail` を済ませている場合など、hookから見えない事情でも、同じコマンドを再実行すれば通るようにする。
- 字句解析は `shlex` による簡易的なもので、`$(...)`・`bash -c "..."` の内部や、`{ ...; }` でまとめたコマンドのパイプは区別しない。

## 備考

- pipefailがないとパイプラインの終了コードは最後のコマンド（`tail`・`grep`・`tee` など）のものになり、テストやビルドが失敗しても成功として返る。エージェントがその結果を根拠に成功を報告する事故を、実行前に防ぐためのhook。[verify-test-pass-claims](../../agentStop/verify-test-pass-claims/README.md) hookはトランスクリプトの終了コードで「テストが通った」という主張を検証するため、終了コードが隠れると検証をすり抜ける。その抜け道を塞ぐ。
- 状態にはコマンドを保存せず、照合用のSHA-256だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-masked-exit-codes/<セッションID>.sqlite3` に保存する。拒否しないコマンドでは状態ファイルを作らない。
- 不正な入力では異常終了する。Copilotはhookの異常終了をツール実行の拒否として扱う。
