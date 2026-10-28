# check-ast-grep hook README

## 発動条件
sessionStart（セッション開始時）

## 処理詳細
1. `ast-grep` コマンドが利用でき、`ast-grep --version` がast-grep本体のバージョンを返すかを確認する。
2. 条件を満たさない場合はエラーを返し、OSに応じた導入方法を案内する。
3. PATHの更新をhookに反映するため、hookを起動するアプリケーションまたはターミナルを再起動を推奨する。

## 備考
- 導入や更新は自動実行しない。
- 最新バージョンかどうかは確認しない。
- 最低バージョンは設けない。ast-grepは1.0未満で頻繁に破壊的変更があり、Ubuntu LTSなど基準にできる標準パッケージもないため、特定のバージョンを要件にする根拠がない。
- 短縮名の `sg` ではなく `ast-grep` を確認する。Linuxでは `sg` がshadow-utilsの同名コマンドと衝突し、ast-grep自体も0.45.0で `sg` を非推奨にしている。
- Linuxでは、ディストリビューションごとにパッケージマネージャーが異なるため、インストールガイドを案内する。npm（`npm install --global @ast-grep/cli`）やpip（`pip install ast-grep-cli`）でも導入できる。
- PATH上で最初に見つかった `ast-grep` を確認する。
