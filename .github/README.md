# .github README

## 前提条件

`.ps1` の実行にはPowerShell 7以降（`pwsh`）が必要。VS CodeのCopilot Chatで使う場合はSession Targetを `Copilot` にする。この場合はCLIと同じSDK hook実装を使うが、利用中のVS Codeが対象イベントに対応するか確認する。`Local` harnessはpayloadと出力仕様が異なるため、現在のhookがそのまま動くとは限らない。

## デバッグモード

ユーザープロンプトの先頭に `??` を付けると、そのセッションで有効になる。次のユーザープロンプトに `??` がなければ解除する。ユーザーの新しい依頼ではない内部プロンプトでは状態を切り替えず、維持する。
