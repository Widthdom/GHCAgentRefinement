# .github README

## 前提条件

このリポジトリのhookはCopilot SDK形式のみを対象とする。`.ps1` の実行にはPowerShell 7以降（`pwsh`）が必要。VS CodeのCopilot Chatで使う場合は、Session Targetを `Copilot` にする。`Copilot` targetはCLIと同じSDK hook実装を使うが、利用中のVS Codeが対象イベントに対応するか確認する。`Local`、`Claude`、`Codex`などSDK以外の実行環境は対象外とし、互換処理やフォールバックを実装せず、動作も検証しない。

## 開発ツールの確認

`check-development-tools` hookは、セッション開始時にGit、ripgrep、Pythonのインストール状況と更新候補を確認する。Windowsではwinget、macOSではHomebrew、Linuxではapt、dnf、pacmanの順に利用可能なパッケージマネージャーを選ぶ。インストールや更新は実行せず、対応が必要な場合に適用可能なコマンドだけを案内する。

## デバッグモード

有効時は `debug-` 接頭辞付きのhookが発動するようになる。デバッグモードの状態は、`userPromptSubmitted` hookがユーザープロンプトを受け取った時点で更新する。ユーザープロンプトの先頭に `??` があれば有効になり、なければ無効になる。`agentStop`後の内部修正では`userPromptSubmitted` hookが発動しないため、直前のユーザープロンプトで設定された状態が維持される。
