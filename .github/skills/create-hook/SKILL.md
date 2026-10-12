---
name: create-hook
description: 'hookを新規作成するときに使う。'
---

1. Copilot SDKのイベント、設定、入出力仕様を使い、SDK以外の形式向けの互換処理、フォールバック、専用の実装・検証は加えない。Windows、macOS、Linuxのすべてを対象にし、対象VS Codeバージョンでイベントを確認する。
2. hook定義を `.github/hooks/<hook-name>.json`、実行・補助スクリプトを `.github/hooks/helpers/<hook-name>/` に配置する。ファイル名にはhook名を使い、実行スクリプトはOSや実装に適した拡張子（例: `.ps1`、`.sh`、`.cmd`）にする。JSONの `bash` / `powershell` コマンドには実行スクリプトのリポジトリルート相対パスを指定する。
3. macOS/Linux用コマンドは `bash`、Windows用コマンドは `powershell` に記述する。各OSで利用するコマンドとスクリプトが動作するようにし、PowerShellを使う場合は `pwsh` を起動する。
4. スクリプト内の補助ファイル参照は `$PSScriptRoot` などを基準にし、呼び出し元の作業ディレクトリに依存させない。PowerShellのパス区切り（`\`、`/`）は文字列に直書きせず、`Join-Path` で階層ごとに連結する。`"$PSScriptRoot\x.ps1"` のような文字列連結や、`Join-Path` の引数に `a\b` のような複数階層を含める書き方は避ける。親ディレクトリには `'..'` でなく `Split-Path -Parent` を使い、一時ファイルは `[System.IO.Path]::GetTempPath()` を基点にする。
5. PowerShell hookの異常終了は原因を標準エラーに出力して `exit 2`、正常終了とスキップは `exit 0` または正常なスクリプト終端にする。Unicode escapeを使う場合は、復号した文字列を近くのコメントに記載する。
6. デバッグ専用hookには `debug-` 接頭辞を付ける。状態形式と保存先は `.github/hooks/helpers/check-debug-mode/check-debug-mode.ps1`、読み取り方は `.github/hooks/helpers/debug-add-response-footer/debug-add-response-footer.ps1` を参照する。入力のセッションIDに対応する状態を読み、ファイルがない場合または `enabled` が `true` でない場合は正常終了する。
7. 実行スクリプトの場所を変更した場合はJSON定義、スクリプト内の相対パス、テストの参照先も更新する。実装後はJSON構文、通常実行、条件を満たさない場合のスキップ、不正入力時の動作を検証する。拒否hookでは許可と拒否の両方を検証し、終了コードを検証するテストも更新する。
