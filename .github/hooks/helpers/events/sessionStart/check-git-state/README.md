# check-git-state hook README

## 発動条件
sessionStart（セッション開始時）

## 処理概要
途中で止まっているmerge・rebaseなどの操作、未解決の衝突、切り離されたHEADを検出し、エージェント経由でユーザーに知らせる。依頼がない限りそれらを操作しないようエージェントに求める。

## 処理詳細
1. 入力の `cwd` で `git rev-parse --absolute-git-dir` を実行し、gitディレクトリを特定する。gitがない、またはリポジトリ外の場合は何もせず正常終了する。
2. gitディレクトリ内の目印から、途中で止まっている操作を検出する。
   |目印|操作|
   |--|--|
   |`MERGE_HEAD`|merge|
   |`rebase-merge/`|rebase|
   |`rebase-apply/`（`applying` あり）|`git am`|
   |`rebase-apply/`（`applying` なし）|rebase|
   |`CHERRY_PICK_HEAD`|cherry-pick|
   |`REVERT_HEAD`|revert|
   |`BISECT_LOG`|bisect|
3. `git ls-files --unmerged` で、衝突が未解決のファイルを最大10件まで列挙する。
4. 途中の操作がない場合に限り、`git symbolic-ref --quiet HEAD` でHEADが切り離されているかを確認する。
5. いずれかに該当した場合は、状態を標準エラーに出力する。同じ状態と、最初の応答の冒頭でユーザーに伝え、依頼がない限り操作の続行・中止・解決やブランチの切り替えをしないよう求める英語の指示を `additionalContext` としてエージェントに渡し、終了コード0で終わる。

## 前提条件
- gitを使う。gitがない、またはリポジトリ外の場合は何もしない。

## 例外
- 未コミットの変更や現在のブランチ名は知らせない。通常の作業でも起きる状態で、毎回の通知が雑音になるため。
- rebaseやbisectの途中はHEADが切り離されるのが通常のため、HEADの切り離しは別に知らせない。

## 制約と限界
- gitの実行は1回5秒、全体で7秒までとし、hookの `timeoutSec`（10秒）より前に打ち切る。時間内に確かめられなかった状態は知らせない。

## 備考
- 途中の操作や切り離されたHEADに気付かないまま作業すると、衝突マーカーの残ったファイルを編集したり、どのブランチにも属さないコミットを作ったりする。これをセッションの最初に知らせるためのhook。
- 目印はworktreeごとのgitディレクトリに置かれるため、worktreeでもそのworktreeの状態を確認する。
- 標準エラーはユーザーに表示されないことがある。sessionStartで解釈される出力は `additionalContext` だけのため、案内はエージェントを介して行う。
- 入力が不正（空、JSONオブジェクトでない、`cwd` が絶対パスの既存ディレクトリでない）な場合は、原因を標準エラーに出力して終了コード2で終わる。
