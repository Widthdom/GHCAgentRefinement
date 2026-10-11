---
name: create-hook
description: 'hookを新規作成するときに使う。'
---

依頼内容と既存設定から、対象となるGitHub Copilotの実行環境（CLIまたはVS Code）とOSを特定する。その環境の公式仕様に合うイベント名と入出力形式を使う。

CLI用hookでは、macOS/Linux向けのコマンドを `bash`、Windows向けのコマンドを `powershell` キーに記述する。`.ps1` を各OSで実行する場合はPowerShell 7以降（`pwsh`）を起動する。VS Code用hookではCLI用の設定形式を流用せず、VS Codeの公式仕様を確認する。

hook定義は `.github/hooks/<hook-name>.json`、実行スクリプトは `.github/hooks/<hook-name>.ps1`、補助スクリプトは `.github/hooks/helpers/<hook-name>/` に配置する。

デバッグモードでのみ動かすhookには `debug-` 接頭辞を付ける。状態の保存先とJSON形式は `check-debug-mode.ps1`、読み取り方は `debug-add-response-footer.ps1` を参照する。hook入力のセッションIDに対応する状態を読み、状態ファイルがない場合または `enabled` が `true` でない場合は、本来の処理を行わず正常終了する。

実装後、JSON構文と、通常実行・実行条件を満たさない場合のスキップ・不正入力時の動作を検証する。実行を拒否するhookでは、許可と拒否の両方を検証する。
