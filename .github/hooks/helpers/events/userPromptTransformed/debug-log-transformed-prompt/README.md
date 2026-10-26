# debug-log-transformed-prompt hook README

## 発動条件

userPromptTransformed（モデルへ渡すプロンプトの変換直後）

## 処理詳細

1. セッションのデバッグモードを確認し、無効なら終了する。
2. 差分を先頭に、ユーザー入力（`prompt`）と変換後の文章（`transformedPrompt`）の全文をMarkdownファイルに保存する。
3. プロンプト自体は変更せず、保存先は独立した[応答終了時のhook](../../agentStop/debug-show-prompt-log-paths/README.md)で表示する。

## 備考

- 保存先は一時フォルダー配下の `ghc-agent-refinement/prompt-transformations/<session-uuid>/`。イベントごとにファイルを作成する。
- プロンプト全文を保存する。差分がない場合も記録する。
- 差分は `diff` コードブロックで表示する（`+` が追加行、`-` が削除行）。
