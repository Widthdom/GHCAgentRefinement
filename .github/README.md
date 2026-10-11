# .github README

## 前提条件

`.ps1` の実行にはPowerShell 7以降（`pwsh`）が必要。このリポジトリのhookをVS CodeのCopilot Chatで使う場合は、Session Targetを `Copilot` にする。`Copilot` targetはCLIと同じSDK hook実装を使うが、利用中のVS Codeが対象イベントに対応するか確認する。`Local`、`Claude`、`Codex` targetではhookの実装やpayloadが異なるため、このリポジトリのhookは前提としていない。

## デバッグモード

ユーザープロンプトの先頭に `??` を付けると、そのセッションで有効になる。次のユーザープロンプトに `??` がなければ解除する。ユーザーの新しい依頼ではない内部プロンプトでは状態を切り替えず、維持する。
