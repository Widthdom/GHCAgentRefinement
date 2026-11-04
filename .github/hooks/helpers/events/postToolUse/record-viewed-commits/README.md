# record-viewed-commits hook README

## 発動条件
postToolUse（ツール `bash`・`powershell` が成功した直後）

## 処理概要
`git show`・`git log` でメッセージを読んだコミットを、[block-regression-of-past-fixes](../../preToolUse/block-regression-of-past-fixes/README.md) hookのために記録する。他のhookが使う記録で、単独では何も出力しない。

## 処理詳細
1. コマンドに `git show`・`git log`（`git -C <dir> show`・`git --no-pager log` なども含む）があるかを調べる。なければ何もしない。
2. 出力（`toolResult.textResultForLlm`）から色付けのエスケープシーケンスを除き、既定の書式の見出し行 `commit <コミットID>` を探す。
3. 見出し行のコミットIDを、このセッションでメッセージを読んだコミットとして [viewed-commits-state.py](../../../utilities/viewed-commits-state/viewed-commits-state.py) に記録する。

## 前提条件
- 特別なプロンプト指定は不要。
- Python 3.14以降の標準の `sqlite3` を使うため、SQLite自体のインストールは不要（公式Windows版Pythonにも同梱）。

## 例外
- `git log --oneline` や `--format` で見出し行を出さない表示は、件名しか読んでいないため記録しない。
- `git show` が失敗した場合（出力に見出し行がない場合）は記録しない。

## 制約と限界
- サブエージェントのコマンドも親と同じ記録に含まれる。postToolUseの入力にはサブエージェントを識別する項目がないため区別できない。
- 出力を `head` などで途中まで表示しても、見出し行があれば読んだとみなす。

## 備考
- 状態は `<一時フォルダー>/ghc-agent-refinement/hook-states/viewed-commits/<セッションID>.sqlite3` に保存する。
