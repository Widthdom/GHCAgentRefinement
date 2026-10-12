---
name: create-hook
description: 'hookを新規作成するときに使う。'
---

このskillで作成するhookはCopilot SDK形式のみを対象とする。依頼内容と既存設定から対象OSを特定し、Copilot SDKのhook仕様に合うイベント名、設定形式、入出力形式を使う。SDK以外の形式への互換処理、フォールバック、専用の実装・検証は追加しない。

GitHub Copilot CLIと、VS CodeでSession Targetに `Copilot` を選んだ場合は、同じCopilot SDK hook実装を使う。CLI向けhook仕様に従い、macOS/Linux向けのコマンドを `bash`、Windows向けのコマンドを `powershell` キーに記述する。`.ps1` を各OSで実行する場合はPowerShell 7以降（`pwsh`）を起動する。VS Codeの対象バージョンでイベントが利用可能か確認する。

VS CodeのSession Targetが `Local`、`Claude`、`Codex` などの場合は対象外とする。設定ファイルが読み込まれる場合でも、SDK以外の実行環境での動作は考慮しない。

hook定義は `.github/hooks/<hook-name>.json`、実行スクリプトと補助スクリプトは `.github/hooks/helpers/<hook-name>/` にまとめて配置する。実行スクリプトは `.ps1`、`.sh`、`.cmd` などOSや実装に適した形式とし、hook名を基にしたファイル名にする。JSON定義の `bash` / `powershell` コマンドには、実行スクリプトのリポジトリルート相対パス（例: `.github/hooks/helpers/<hook-name>/<hook-name>.ps1`）を記述する。スクリプトから補助スクリプトや設定ファイルを参照するときは、呼び出し元の作業ディレクトリに依存しないよう `$PSScriptRoot` などを基準にする。PowerShellスクリプトのパス組み立てはWindows/macOS/Linuxで動作するよう、次に従う。パス区切りの `\` や `/` を文字列に直書きせず、`Join-Path` で階層ごとに連結する（例: `Join-Path $PSScriptRoot 'common.ps1'`、`Join-Path (Join-Path $base 'a') 'b'`）。`"$PSScriptRoot\x.ps1"` のような文字列連結や、`Join-Path` の引数に `a\b` を含める書き方は使わない。`'..'` による親ディレクトリ指定も避け、`Split-Path -Parent $PSScriptRoot` を使う。一時ファイルの基点は `[System.IO.Path]::GetTempPath()` を使う。実行スクリプトの場所を変更した場合は、JSON定義、スクリプト内の相対パス、およびテストの参照先も合わせて更新する。

PowerShell hookで文字列をUnicode escapeで記述する場合は、escapeを復号した元の文字列を近くのコメントにそのまま記載する。

PowerShell hookの異常終了はすべて `exit 2` に統一し、原因を標準エラーへ出力する。Copilot SDKを前提とする場合もこの方針に従う。SDK/CLIの `preToolUse` コマンドhookは非ゼロ終了を拒否扱いにするが、VS Code Localでは `exit 1` は非ブロッキング警告扱いとなり、`exit 2` がブロッキングエラーを表すためである。当然VS Code Localは前提としないが `exit 2` に統一することにする。正常終了や対象外のスキップには `exit 0` または正常なスクリプト終端を使い、終了コードを検証するテストも合わせて更新する。

デバッグモードでのみ動かすhookには `debug-` 接頭辞を付ける。状態の保存先とJSON形式は `.github/hooks/helpers/check-debug-mode/check-debug-mode.ps1`、読み取り方は `.github/hooks/helpers/debug-add-response-footer/debug-add-response-footer.ps1` を参照する。hook入力のセッションIDに対応する状態を読み、状態ファイルがない場合または `enabled` が `true` でない場合は、本来の処理を行わず正常終了する。

実装後、JSON構文と、通常実行・実行条件を満たさない場合のスキップ・不正入力時の動作を検証する。実行を拒否するhookでは、許可と拒否の両方を検証する。
