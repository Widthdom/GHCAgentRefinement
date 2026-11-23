# block_masked_exit_codes hook README

## 発動条件
preToolUse（ツール `bash` の実行前）

## 処理概要
テスト・ビルド・静的検査の終了コードが、`pipefail` なしのパイプや、後に続く `;`・`||`・`&` で隠れるコマンドを拒否する。

## 処理詳細
1. 呼び出すツール（`toolName`・`toolArgs`）がbashなら、そのコマンドをパイプライン（`|`・`|&` でつないだコマンドの列）に分ける。`;`・`&&`・`||`・`&`・括弧・改行でパイプラインが終わる。
2. 最後の段以外にテスト・ビルド・静的検査のコマンドがあり、それより前で `set -o pipefail` が有効になっていないパイプラインを探す（パイプで隠れるもの）。
3. 2.に当たらないテスト・ビルド・静的検査のコマンドのうち、後に `;`・改行・`||` で別のコマンドが続くもの（`set -e`・`set -o errexit` がある場合の `;`・改行を除く）と、`&` でバックグラウンドで実行するものを探す（後続のコマンドで隠れるもの）。判定には、[check_test_runs](../../../utilities/check_test_runs/README.md) の `get_masked_test_runs` を使う。
4. 2.・3.に該当するコマンドがあり、このセッションで同じコマンドをまだ拒否していなければ、英語の理由とともに `{"permissionDecision":"deny",...}` を標準出力して拒否する。理由には、該当した場合ごとに次の対処を書く。
   - パイプで隠れるもの：`set -o pipefail;` を付けるか、出力をファイルに書き出して別のツール呼び出しで確認する。
   - 後続のコマンドで隠れるもの：テスト・ビルドを単独で実行し（出力のファイルへのリダイレクトは可）、出力や終了コードは別のツール呼び出しで確認するか、成功したときだけ続けるなら `&&` でつなぐ。

## テスト・ビルド・静的検査とみなすコマンド
- 段の語に `test`・`tests`・`pytest`・`unittest`・`jest`・`vitest`・`mocha`・`rspec`・`phpunit`・`ctest`・`tox`・`pester`・`build`・`msbuild`・`make`・`tsc`・`lint`・`eslint`・`ruff`・`mypy`・`clippy` を含むもの（`dotnet test`・`npm run build`・`python3 test_all_hooks.py` など）。テストの語は、[check_test_runs](../../../utilities/check_test_runs/README.md) がテストの実行とみなす語と同じ。
- 前置きの読み飛ばしと、テストの実行とみなさないコマンドの除外は、[check_test_runs](../../../utilities/check_test_runs/README.md) の規則を使う。先頭の環境変数の代入（`CI=1`）、`env`・`sudo`・`time`・`nohup`・`xargs`・`timeout <時間>` などのラッパーは読み飛ばし、その後のコマンドで判定する。
- `cat`・`grep`・`rg`・`find`・`ls`・`git`・`head`・`tail` などの読むだけのコマンド、`mkdir`・`cp`・`mv`・`rm`・`touch` などのファイルを操作するだけのコマンド、条件を判定する組み込みコマンド（`test`・`[`・`[[`）は、`grep test`・`mkdir tests` のように語を含んでも対象外とする。

## 例外
- 次のコマンドは許可する。
  - 同じコマンド内で、パイプラインより前に `set -o pipefail`・`set -euo pipefail` などを実行しているもの（後の `set +o pipefail` で無効に戻る）
  - `PIPESTATUS` を参照して各段の終了コードを確かめているもの。ただし、その参照を `;` でつないだ場合（`pytest | tail; echo "${PIPESTATUS[0]}"`）は、後続のコマンドで隠れるものとして拒否する。
  - テスト・ビルドがパイプラインの最後の段にあるもの（`cat input | pytest`）。終了コードがそのまま伝わるため。
  - テスト・ビルドの後が `&&` だけで続くか、テスト・ビルドで終わるもの（`pytest && echo ok`・`cd x; pytest`・`pytest > test.log 2>&1`）
  - 引用符の中、コメント、ヒアドキュメントの本文に書かれた `|`・`;`
- powershellは対象外とする。ネイティブコマンドの終了コードは `$LASTEXITCODE` に残り、パイプラインの最後のコマンドで上書きされるとは限らないため。

## 制約と限界
- 拒否は同じコマンドにつきセッションで1回だけ。前のツール呼び出しで `set -o pipefail` を済ませている場合など、hookから見えない事情でも、同じコマンドを再実行すれば通る。
- 字句解析は `shlex` による簡易的なもので、`$(...)`・`bash -c "..."` の内部や、`{ ...; }` でまとめたコマンドのパイプは区別しない。
- `; echo "x=$?"` のように、後続のコマンドで終了コードを表示する場合も拒否する。表示された値は人が読めば分かるが、[verify_test_pass_claims](../../agentStop/verify_test_pass_claims/README.md) hookはコマンド全体の終了コードしか根拠にしないため、実行前の判定もそれに揃える。

## 備考
- pipefailがないとパイプラインの終了コードは最後のコマンド（`tail`・`grep`・`tee` など）のものになり、`;`・`||` で続けるとコマンド全体の終了コードは後続のコマンドのものになる。どちらもテストやビルドが失敗しても成功として返る。エージェントがその結果を根拠に成功を報告する事故を、実行前に防ぐためのhook。
- [verify_test_pass_claims](../../agentStop/verify_test_pass_claims/README.md)・[require_hook_tests](../../agentStop/require_hook_tests/README.md) hookは、終了時にトランスクリプトの終了コードで「テストが通った」という主張やhookのテストの実行を検証し、終了コードが隠れた実行を根拠として認めない。実行前に同じ基準で止めることで、終了時に指摘されてからテストをやり直す手戻りを防ぐ。
- 状態にはコマンドを保存せず、照合用のSHA-256だけを `<一時フォルダー>/ghc_agent_refinement/hook_states/block_masked_exit_codes/<セッションID>.sqlite3` に保存する。拒否しないコマンドでは状態ファイルを作らない。
- hookの異常終了（不正な入力）は、Copilotがツール実行の拒否として扱う。
