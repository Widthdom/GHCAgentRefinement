# check-test-runs utility README

## 役割
シェルのコマンドがテストを実行するか、その終了コードがテストの結果を表すか、応答が「テストが通った」と書いているかを判定する。テストの実行結果を扱うhookが共有し、判定の基準を1か所で管理する。

## テストの実行の判定
- コマンドを [split-bash-lines](../split-bash-lines/split-bash-lines.py) で字句に分け、`;`・`&&`・`||`・`|`・`&`・改行・括弧で単純コマンドに区切る。PowerShellのパスの区切りの `\` は、英数字などの前にあれば `/` とみなす。
- 単純コマンドごとに、環境変数の代入（`FOO=1`）と前置きのコマンド（`sudo`・`env`・`time`・`timeout <時間>`・`xargs` など）とオプションを読み飛ばし、実際に実行するコマンド以降の語を取り出す（`get_command_words`）。
- 取り出したコマンドが読むだけのコマンド・移動するだけのコマンドなら、テストの実行とみなさない。`grep test`・`ls tests`・`cat test-x.py`・`git add test-x.py`・`cd tests` などが該当する。
  - 対象：`ag`・`awk`・`cat`・`cd`・`cut`・`diff`・`du`・`echo`・`egrep`・`fd`・`fgrep`・`file`・`find`・`git`・`grep`・`head`・`jq`・`less`・`ls`・`more`・`printf`・`pushd`・`rg`・`sed`・`sort`・`stat`・`tail`・`tr`・`tree`・`uniq`・`wc`・`which`・`yq`、PowerShellの `Get-ChildItem`・`Get-Content`・`Select-String`・`Set-Location`・`Write-Host`・`Write-Output`（大文字小文字を区別しない）
- それ以外で、語のいずれかに `test`・`tests`・`pytest`・`unittest`・`jest`・`vitest`・`mocha`・`rspec`・`phpunit`・`ctest`・`tox`・`pester`（`TEST_COMMAND_WORDS`）を含めばテストの実行とみなす（`is_test_command`）。`latest`・`attest` などの語中は除く。hookは、テストファイル名など別の正規表現を指定して判定することもできる。

## 終了コードがテストの結果を表すかの判定
`is_test_result_reported` は、テストを実行する単純コマンドのすべてについて、コマンド全体の終了コードがその結果を表す場合にだけ真を返す。次のいずれかに当てはまるテストは、終了コードが0でも成功の根拠にならないとみなす。

|条件|例|
|--|--|
|テストの後に `;`・改行で別のコマンドが続く（`set -e`・`set -o errexit` がある場合を除く）|`python3 test-x.py; echo done`|
|テストの後に `\|\|` が続く|`python3 test-x.py \|\| true`|
|バックグラウンドで実行する|`pytest &`|
|pipefailなしでパイプの途中にある（`pipefail`・`PIPESTATUS` がコマンドにある場合を除く）|`npm test \| tail -5`|

- `pytest && echo ok`・`cd x; pytest`・`pytest > /tmp/log 2>&1` のように、テストの後が `&&` だけで続くか、テストで終わる場合は結果を表すとみなす。

## テストが通ったという主張の判定
- `claims_tests_passed` は、応答が「テストが通った」「All tests passed」などと書いているかを判定する。コードブロック・引用行・引用符やインラインコードの中は除き、直後に否定（「通っていない」など）が続く場合も主張とみなさない。

## 利用するhook
- [verify-test-pass-claims](../../events/agentStop/verify-test-pass-claims/README.md)
- [require-hook-tests](../../events/agentStop/require-hook-tests/README.md)
- [record-test-failure-values](../../events/postToolUse/record-test-failure-values/README.md)
- [detect-vacuous-test-success](../../events/postToolUse/detect-vacuous-test-success/README.md)
- [block-masked-exit-codes](../../events/preToolUse/block-masked-exit-codes/README.md)
