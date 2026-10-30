# check-python hook README

## 発動条件
sessionStart（セッション開始時）

## 処理詳細
1. macOS/Linuxでは `python3`、Windowsでは `python` が利用できるか、Pythonのバージョンが3.14以降かを確認する。
2. 条件を満たさない場合は、OSに応じた導入方法を標準エラーに出力する。同じ案内と、最初の応答の冒頭でユーザーに伝えるよう求める英語の指示を `additionalContext` としてエージェントに渡し、終了コード0で終わる。
3. PATHの更新をhookに反映するため、hookを起動するアプリケーションまたはターミナルを再起動を推奨する。

## 備考
- 導入や更新は自動実行しない。
- 標準エラーはユーザーに表示されないことがある。sessionStartで解釈される出力は `additionalContext` だけで、標準出力は終了コード0のときだけ読まれるため、案内はエージェントを介して行う。
- Pythonを使えないため、`additionalContext` のJSONはsh・cmdで手で組み立てる。案内の文言には、JSONやcmdでエスケープが必要な文字を含めない。
- Python 3.14を最低対応・推奨バージョンとする。
- 最新バージョンかどうかは確認しない。
- Python未インストール前提のため、Windowsでは [cmd](./check-python.cmd)、macOS/Linuxでは [sh](./check-python.sh) を呼び出す。
- Linuxでは、ディストリビューションごとにパッケージマネージャーが異なるため、インストールガイドを案内する。
- Windowsでのインストール後はPATHの更新をhookに反映するため、hookを起動するアプリケーションまたはターミナルを再起動すること。
- macOS/Linuxでのインストール後に `python3` が見つからない場合は、PATHの更新を反映するため、hookを起動するアプリケーションまたはターミナルを再起動すること。
- Windows用バッチはコメントも含めASCIIで記述する。UTF-8の日本語コメントは、cmd.exeのコードページによってコメントの一部がコマンドとして解釈されるため、使用しない。

### check_python_version.pyの呼び出しを `call python "%%check_python_version_path%%"` とする理由
- Windowsでは、`python.exe` でなく `python.cmd` が使用される場合がある。
  - 後者の場合、call無しで実行すると呼び出し元に戻らないため、callで実行する必要がある。
- callは構文解析時（1度目）と実行時（2度目）の計2回、パーセント記号（%）の変数展開を行う仕様になっている。
  - %%check_python_version_path%% のようにすることで、仮にcheck_python_version_pathの中に文字通り `%USERPROFILE%` のようなフォルダ名が含まれていても、callの2度の展開に耐える、pathの崩れを回避可能。
