# check-python hook README

## 発動条件
sessionStart（セッション開始時）

## 処理詳細
1. macOS/Linuxでは `python3`、Windowsでは `python` が利用できるか、Pythonのバージョンが3.14以降かを確認する。
2. 条件を満たさない場合はエラーを返し、OSに応じた導入方法を案内する。

## 備考
- 導入や更新は自動実行しない。
- Windowsでは [cmd](./check-python.cmd)、macOS/Linuxでは [sh](./check-python.sh) を呼び出す。
- Python 3.14を最低対応・推奨バージョンとする。Windowsでは `winget install Python.Python.3.14 --source winget` で3.14系列を導入し、`python` から実行する。インストール後はPATHの更新をhookに反映するため、hookを起動するアプリケーションまたはターミナルを再起動する。
- Linuxの場合はインストールガイドの案内となる。
- macOS/Linuxでも、インストール後に `python3` が見つからない場合は、PATHの更新を反映するため、hookを起動するアプリケーションまたはターミナルを再起動する。
