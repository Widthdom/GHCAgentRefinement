---
name: create-hook
description: 'hookを新規作成するときに使う。'
---

依頼内容と既存設定から、対象となる実行環境、VS Codeの場合はSession Target（harness）、およびOSを特定する。その環境の公式仕様に合うイベント名、設定形式、入出力形式を使う。

GitHub Copilot CLIと、VS CodeでSession Targetに `Copilot` を選んだ場合は、同じCopilot SDK hook実装を使う。CLI向けhook仕様に従い、macOS/Linux向けのコマンドを `bash`、Windows向けのコマンドを `powershell` キーに記述する。`.ps1` を各OSで実行する場合はPowerShell 7以降（`pwsh`）を起動する。VS Codeの対象バージョンでイベントが利用可能か確認する。

VS CodeのSession Targetが `Local` の場合は別のhook実装を使うため、VS Code Local harnessの公式仕様に従う。Copilot形式の設定ファイルがLocalで読み込まれる場合もあるが、実行時のイベント名、payload、出力仕様までCopilot SDKと同じとは限らないため、Local用の形式とschemaで実装・検証する。`Claude` や `Codex` など別providerのtargetはこのskillの対象外とし、それぞれの公式hook仕様を使う。

hook定義は `.github/hooks/<hook-name>.json`、実行スクリプトは `.github/hooks/<hook-name>.ps1`、補助スクリプトは `.github/hooks/helpers/<hook-name>/` に配置する。

PowerShell hookで文字列をUnicode escapeで記述する場合は、escapeを復号した元の文字列を近くのコメントにそのまま記載する。

デバッグモードでのみ動かすhookには `debug-` 接頭辞を付ける。状態の保存先とJSON形式は `check-debug-mode.ps1`、読み取り方は `debug-add-response-footer.ps1` を参照する。hook入力のセッションIDに対応する状態を読み、状態ファイルがない場合または `enabled` が `true` でない場合は、本来の処理を行わず正常終了する。

実装後、JSON構文と、通常実行・実行条件を満たさない場合のスキップ・不正入力時の動作を検証する。実行を拒否するhookでは、許可と拒否の両方を検証する。
