# request-adversarial-review hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理詳細
1. [set-adversarial-review-state](../../userPromptSubmitted/set-adversarial-review-state/README.md) による状態を取得する。
2. 敵対的レビューモードが無効なら終了する。
3. レビュー待ち状態でない場合は終了する。
4. `git status --porcelain=v1 --untracked-files=all -z` を実行し変更差分を確認する。
5. 変更差分が無ければ終了する。
6. レビュー待ち状態を解除する。
7. `{"decision":"block","reason":"..."}` を標準出力する。
8. hook呼び出し元は標準出力を受け取り、decision:blockにより応答を終了せずに継続し、reasonに従って [敵対的レビューエージェント](../../../../../agents/adversarial-review.agent.md) へレビューを依頼する。
