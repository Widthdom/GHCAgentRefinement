# create-hook skill README

## 対象ランタイム

VS CodeのSession Targetが `Copilot` の場合はCLIと同じSDK hook実装を使うため、SDKの仕様が両方に適用できる。一方、`Local`、`Claude`、`Codex`は異なる実行環境であり、同じ設定が読み込まれても互換性があるとは限らない。VS Codeのバージョンによってイベントの対応状況が異なることもある。

Windows、macOS、Linuxではコマンドやパスの扱いが異なる。

## 実装言語

hookの実行スクリプトをPythonに統一すると、Windows、macOS、Linuxで同じスクリプトを `python3` から起動でき、OSごとにシェルスクリプトを書き分ける必要がなくなる。JSON・正規表現・パス操作・一時ディレクトリなど、hookで必要な処理は標準ライブラリだけで扱える。

## Windows特有の制御

サービスの制御やレジストリ、CIM/WMIなどのWindows管理機能は、Windows PowerShellのコマンドレット（`Get-Service`、`Restart-Service` など）から直接扱える。Pythonで同じことをするには外部コマンドの呼び出しや追加パッケージが必要になる。Windows PowerShell 5.1はWindowsに標準搭載されており、PowerShell 7のような追加インストールも不要なため、Windowsでしか意味を持たない処理は5.1で実装する。5.1ではPowerShell 7以降で追加された構文や機能を使えず、`Set-Content -Encoding UTF8` などはBOM付きで書き込むため、エンコーディングを明示する。

## スクリプト配置とパス

hook定義とスクリプトをhook名ごとにまとめることで、関連ファイルの対応関係を保ちやすくなる。hookから起動するスクリプトは呼び出し元の作業ディレクトリを前提にできないため、補助ファイルはスクリプト自身の場所（`__file__`）を基準に参照する。`os.path.join` と標準パスAPIを使うことで、Windows、macOS、Linux間のパス区切りや親ディレクトリ表記の違いに依存しなくなる。一時ファイルにはOSが提供する一時ディレクトリを使う。

## 入出力のエンコーディング

Pythonの標準入出力の既定エンコーディングはOSやロケールに依存し、Windowsでは日本語などを含むJSONを正しく読み書きできない場合がある。標準入力をバイト列で読んでUTF-8で復号し、出力もBOMなしのUTF-8に揃えることで、OSによらず同じJSONを扱える。

## 終了コード

Copilot SDK/CLIの `preToolUse` command hookでは非ゼロ終了が拒否扱いになる一方、VS Code Localでは `exit 1` が非ブロッキング警告、`exit 2` がブロッキングエラーを表す。Localは対象外だが、異常終了を `exit 2` に統一すると失敗の意味が明確になり、hook間の扱いも揃う。

## デバッグモードの状態

セッションIDごとにデバッグ状態を読むことで、別セッションの設定を引き継がず、明示的に有効化されたセッションだけでデバッグ専用hookを実行できる。状態がない場合や無効の場合にスキップすれば、通常利用への影響を避けられる。
