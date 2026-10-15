# check-development-tools hook README

## 発動条件
sessionStart（セッション開始時）

## 処理詳細
1. Git、ripgrepのインストール有無とバージョンを確認する。
2. 未インストールツールに対してはインストールコマンドを、更新が必要なツールに関しては更新コマンドを案内する。

## 備考
- Windowsではwinget、macOSではHomebrew、Linuxではapt、dnf、pacmanの順に利用可能なパッケージマネージャーを選ぶ。
- hookの実行にPowerShell 7以降は不要なため、PowerShellのインストール有無や更新は確認しない。
- Pythonの最低バージョンは別の[check-python hook](../check-python/README.md)で確認し、このhookではPythonの更新を確認しない。
- バージョン比較では省略された末尾の桁を0として扱う。例えば`7.6.6`と`7.6.6.0`は同じバージョンとみなし、更新を案内しない。
- インストールや更新は自動実行しない。
