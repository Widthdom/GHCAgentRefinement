# check-development-tools hook README

## 発動条件
sessionStart（セッション開始時）

## 処理詳細
1. PowerShell 7以降のバージョンを確認する。
2. Git、ripgrep、Pythonのインストール有無とバージョンを確認する。
3. 未インストールツールに対してはインストールコマンドを、更新が必要なツールに関しては更新コマンドを案内する。

## 備考
- Windowsではwinget、macOSではHomebrew、Linuxではapt、dnf、pacmanの順に利用可能なパッケージマネージャーを選ぶ。
- PowerShellはWindowsではwinget、macOSではHomebrew cask、Linuxでは選択したシステムパッケージマネージャーを使って最新版を確認する。
- バージョン比較では省略された末尾の桁を0として扱う。例えば`7.6.6`と`7.6.6.0`は同じバージョンとみなし、更新を案内しない。
- インストールや更新は自動実行しない。
