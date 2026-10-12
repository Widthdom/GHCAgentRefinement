# .github README

## 前提条件

このリポジトリのhookはCopilot SDK形式のみを対象とする。`.ps1` の実行にはPowerShell 7以降（`pwsh`）が必要。VS CodeのCopilot Chatで使う場合は、Session Targetを `Copilot` にする。`Copilot` targetはCLIと同じSDK hook実装を使うが、利用中のVS Codeが対象イベントに対応するか確認する。`Local`、`Claude`、`Codex`などSDK以外の実行環境は対象外とし、互換処理やフォールバックを実装せず、動作も検証しない。

## PowerShell 7以降のインストールの案内

セッション開始時にPowerShell 7以降のインストール有無を確認し、無ければユーザにインストールコマンド実行を推奨する。詳細は[check-powershell hookのREADME](./hooks/helpers/check-powershell/README.md)を参照。

## 各種開発ツールのインストールと更新の案内

セッション開始時に各種開発ツールのインストール有無とバージョンを確認し、必要であればユーザにコマンド実行を推奨する。詳細は[check-development-tools hookのREADME](./hooks/helpers/check-development-tools/README.md)を参照。

## デバッグモード

プロンプトの先頭に `??` を付与するとデバッグモードになり、 `debug-` 接頭辞付きのhookが発動するようになる。詳細は[check-debug-mode hook README](./hooks/helpers/check-debug-mode/README.md)を参照。
