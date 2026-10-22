# set-debug-mode hook README

## 発動条件
userPromptSubmitted（ユーザがプロンプトを投入する度）

## 処理詳細
プロンプト先頭が `??` であった場合にデバッグモードを有効にする。そうでない場合は無効にする。

## 備考
- デバッグモードが無効である限り、`debug-` 接頭辞付きのhookは発動しない。
- `agentStop`後の内部修正では `userPromptSubmitted` hookが発動しないため、直前のユーザプロンプトで設定されたデバッグモード有効/無効状態が維持される。
