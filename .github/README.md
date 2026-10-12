# .github README

## 前提条件

このリポジトリのhookはCopilot SDK形式のみを対象とする。`.ps1` の実行にはPowerShell 7以降（`pwsh`）が必要。VS CodeのCopilot Chatで使う場合は、Session Targetを `Copilot` にする。`Copilot` targetはCLIと同じSDK hook実装を使うが、利用中のVS Codeが対象イベントに対応するか確認する。`Local`、`Claude`、`Codex`などSDK以外の実行環境は対象外とし、互換処理やフォールバックを実装せず、動作も検証しない。

## 主なhook

### セッション開始時発動

|hook|概要|参考|
|--|--|--|
|Session Targetの確認|Session Targetが `Copilot` の状態で使用できるよう支援する。|[check-target-session hook README](./hooks/helpers/check-target-session/README.md)|
|Powershellの確認|Powershell 7以降のインストールを支援する。|[check-powershell hook README](./hooks/helpers/check-powershell/README.md)|
|開発ツールの確認|各種開発ツールのインストールとアップデートを支援する。|[check-development-tools hook README](./hooks/helpers/check-development-tools/README.md)|
|Copilot Instructionsの読み込み|`copilot-instructions.md` を明に追加コンテキストとして渡す。|[load-copilot-instructions hook README](./hooks/helpers/load-copilot-instructions/README.md)|

### bashまたはpowershellツールの実行前に発動

|hook|概要|参考|
|--|--|--|
|危険コマンドの実行拒否|[`blocked-command-patterns.json`](./hooks/helpers/block-commands/blocked-command-patterns.json)にて定義されたコマンドの実行を拒否する。|[block-commands hook README](./hooks/helpers/block-commands/README.md)|

## デバッグモード

プロンプトの先頭に `??` を付与するとデバッグモードになり、 `debug-` 接頭辞付きのhookが発動するようになる。詳細は[check-debug-mode hook README](./hooks/helpers/check-debug-mode/README.md)を参照。
