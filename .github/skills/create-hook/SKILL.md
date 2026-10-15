---
name: create-hook
description: 'hookを新規作成するときに使う。'
---

1. Copilot SDKのイベント、設定、入出力仕様を使い、SDK以外の形式向けの互換処理、フォールバック、専用の実装・検証は加えない。Windows、macOS、Linuxのすべてを対象にし（9.のWindows特有の制御を行うhookを除く）、対象VS Codeバージョンでイベントを確認する。
2. hook定義を `.github/hooks/<hook-name>.json`、実行・補助スクリプトを `.github/hooks/helpers/<hook-name>/` に配置する。ファイル名にはhook名を使い、実行スクリプトはPython（`.py`）で作成する。JSONの `bash` / `powershell` コマンドには実行スクリプトのリポジトリルート相対パスを指定する。
3. 処理自体が複雑で説明が必要な場合や、設計背景を補足する必要がある場合は `.github/hooks/helpers/<hook-name>/README.md` を作成し、動作や背景を記載する。
4. macOS/Linux用コマンドは `bash`、Windows用コマンドは `powershell` に記述する。macOS/Linuxでは `python3`、Windowsでは `python` を使って `.py` スクリプトを起動する。各OSで同じスクリプトが動作するようにし、OS固有のシェル構文に依存させない。
5. hookの最低対応Pythonバージョンと推奨バージョンは3.14とする。構文と標準ライブラリAPIはPython 3.14で利用できるものに限定し、新しいバージョン固有の機能を使わない。新規インストールではPython 3.14系列の最新パッチ版を案内し、新しいメジャー・マイナーバージョンが公開されても、この基準を明示的に更新するまでは推奨を切り替えない。
6. スクリプト内の補助ファイル参照は `os.path.dirname(os.path.abspath(__file__))` を基準にし、呼び出し元の作業ディレクトリに依存させない。パス区切り（`\`、`/`）は文字列に直書きせず、`os.path.join` で階層ごとに連結する。`script_directory + '\\x.py'` のような文字列連結や、`os.path.join` の引数に `'a/b'` のような複数階層を含める書き方は避ける。親ディレクトリには `'..'` でなく `os.path.dirname` を使い、一時ファイルは `tempfile.gettempdir()` を基点にする。
7. 標準入力はバイト列として読み、UTF-8で復号する。標準出力・標準エラーはBOMなしのUTF-8で出力する。異常終了は原因を標準エラーに出力して `sys.exit(2)`、正常終了とスキップは `sys.exit(0)` または正常なスクリプト終端にする。Unicode escapeを使う場合は、復号した文字列を近くのコメントに記載する。
8. デバッグ専用hookには `debug-` 接頭辞を付ける。状態形式と保存先は `.github/hooks/helpers/check-debug-mode/check-debug-mode.py`、読み取り方は `.github/hooks/helpers/debug-add-response-footer/debug-add-response-footer.py` を参照する。入力のセッションIDに対応する状態を読み、ファイルがない場合または `enabled` が `true` でない場合は正常終了する。
9. 実行スクリプトの場所を変更した場合はJSON定義、スクリプト内の相対パス、テストの参照先も更新する。テストは `.github/hooks/helpers/<hook-name>/test-<hook-name>.py` に作成し、macOS/Linuxでは `python3`、Windowsでは `python` で実行する。Python 3.14でテストし、JSON構文、通常実行、条件を満たさない場合のスキップ、不正入力時の動作を検証する。拒否hookでは許可と拒否の両方を検証し、終了コードを検証するテストも更新する。
10. Windows特有の制御（サービスの制御など）を行うhookは、例外としてWindows PowerShell 5.1で `.ps1` を作成する。JSONの `powershell` にのみ `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .github/hooks/helpers/<hook-name>/<hook-name>.ps1` を記述し、`bash` は記述しない。PowerShell 7以降専用の構文や機能（`??`、`?.`、三項演算子、`$IsWindows`、`ConvertFrom-Json -AsHashtable` など）は使わない。補助ファイル参照は `$PSScriptRoot` を基準に `Join-Path` で階層ごとに連結し、標準入出力のエンコーディングとファイル書き込みはBOMなしのUTF-8（`[System.Text.UTF8Encoding]::new($false)`）を明示する。異常終了は原因を標準エラーに出力して `exit 2` にする。テストは `test-<hook-name>.ps1` に作成し、`powershell -NoProfile -File` で実行する。
