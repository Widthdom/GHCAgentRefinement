# request-adversarial-review hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
敵対的レビューモードでレビュー待ちのとき、作業ツリーに変更があれば応答の終了を止め、敵対的レビューエージェントへのレビュー依頼を促す。

## 処理詳細
1. [set-adversarial-review-state](../../userPromptSubmitted/set-adversarial-review-state/README.md) による状態を取得する。
2. 敵対的レビューモードが無効なら終了する。
3. レビュー待ち状態でない場合は終了する。
4. `git status --porcelain=v1 --untracked-files=all -z` を実行し変更差分を確認する。
5. 変更差分が無ければ終了する。
6. レビュー待ち状態を解除する。状態ファイルは [write-json-atomically](../../../utilities/write-json-atomically/README.md) で一時ファイルに書いてから置き換える。
7. `{"decision":"block","reason":"..."}` を標準出力する。
8. hook呼び出し元は標準出力を受け取り、decision:blockにより応答を終了せずに継続し、reasonに従って [敵対的レビューエージェント](../../../../../agents/adversarial-review.agent.md) へレビューを依頼する。

## 前提条件
- プロンプト末尾に `;;` を付けて、[set-adversarial-review-state](../../userPromptSubmitted/set-adversarial-review-state/README.md) hookで敵対的レビューモードを有効にしたときだけ動く。
