# check-python hook README

## 発動条件
sessionStart（セッション開始時）

## 処理詳細
1. macOS/Linuxでは `python3`、Windowsでは `python` が利用できるか、Pythonのバージョンが3.14以降かを確認する。
2. 条件を満たさない場合はエラーを返し、OSに応じた導入方法を案内する。
3. PATHの更新をhookに反映するため、hookを起動するアプリケーションまたはターミナルを再起動を推奨する。

## 備考
- 導入や更新は自動実行しない。
- Python 3.14を最低対応・推奨バージョンとする。
- 最新バージョンかどうかは確認しない。
- Python未インストール前提のため、Windowsでは [cmd](./check-python.cmd)、macOS/Linuxでは [sh](./check-python.sh) を呼び出す。
- Linuxでは、ディストリビューションごとにパッケージマネージャーが異なるため、インストールガイドを案内する。
- Windowsでのインストール後はPATHの更新をhookに反映するため、hookを起動するアプリケーションまたはターミナルを再起動すること。
- macOS/Linuxでのインストール後に `python3` が見つからない場合は、PATHの更新を反映するため、hookを起動するアプリケーションまたはターミナルを再起動すること。
