# .github README

## 前提条件

`.ps1` の実行にはPowerShell 7以降（`pwsh`）が必要。このリポジトリのhookをVS CodeのCopilot Chatで使う場合は、Session Targetを `Copilot` にする。`Copilot` targetはCLIと同じSDK hook実装を使うが、利用中のVS Codeが対象イベントに対応するか確認する。`Local`、`Claude`、`Codex` targetではhookの実装やpayloadが異なるため、このリポジトリのhookは前提としていない。

## デバッグモード

有効時は `debug-` 接頭辞付きのhookが発動するようになる。デバッグモードの状態は、`userPromptSubmitted` hookがユーザープロンプトを受け取った時点で更新する。ユーザープロンプトの先頭に `??` があれば有効になり、なければ無効になる。`agentStop`後の内部修正では`userPromptSubmitted` hookが発動しないため、直前のユーザープロンプトで設定された状態が維持される。
