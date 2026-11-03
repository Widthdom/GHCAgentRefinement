# check-required-tools hook README

## 発動条件
sessionStart（セッション開始時）

## 処理概要
hookとエージェントが使うコマンドラインツール（ast-grep・Git・jq・ripgrep）が使えるかを確認し、使えないものがあれば導入方法をエージェント経由でユーザーに案内させる。

## 処理詳細
1. [required-tool-definitions.json](./required-tool-definitions.json) から、ツールの表示名（`name`）、コマンド（`command`）、バージョンの表示を読み取る正規表現（`versionPattern`）、最低バージョン（`minimumVersion`）、OSごとの導入方法（`install`）を読み込む。ファイルがない、形式が不正、正規表現・バージョンが不正な場合は異常終了する。
2. ツールごとに `<command> --version` を実行し、コマンドが利用できるか、出力の先頭が `versionPattern` に一致するか、一致したバージョンが `minimumVersion` 以上かを確認する。`minimumVersion` が `null` のツールは、バージョンの表示が一致すれば条件を満たすとする。
3. 条件を満たさないツールがあれば、定義の順に、必要なバージョンとOSに応じた導入方法を並べて標準エラーに出力する。同じ案内と、最初の応答の冒頭でユーザーに伝えるよう求める英語の指示を `additionalContext` としてエージェントに渡し、終了コード0で終わる。
4. PATHの更新をhookに反映するため、hookを起動するアプリケーションまたはターミナルの再起動を、案内の最後に1回だけ推奨する。

## 確認するツール
|ツール|コマンド|最低バージョン|根拠|
|--|--|--|--|
|ast-grep|`ast-grep`|なし|1.0未満で頻繁に破壊的変更があり、Ubuntu LTSなど基準にできる標準パッケージもないため、特定のバージョンを要件にする根拠がない。|
|Git|`git`|2.34.1|標準サポート期間中で最も古いUbuntu LTS（22.04）の標準パッケージ。|
|jq|`jq`|1.6|同上。|
|ripgrep|`rg`|13.0.0|同上。|

## 制約と限界
- 導入や更新は自動実行しない。
- 最新バージョンかどうかは確認しない。
- PATH上で最初に見つかったコマンドを確認する。ripgrepでは、VS Code同梱のripgrepかどうかは区別しない（[トラブルシューティング](#トラブルシューティング)を参照）。
- Pythonは確認しない。このhookはPythonで動くため、Pythonがない環境でも案内できるよう、[check-python](../check-python/README.md) hookが `.sh`・`.cmd` で確認する。

## 備考
- 標準エラーはユーザーに表示されないことがある。sessionStartで解釈される出力は `additionalContext` だけで、標準出力は終了コード0のときだけ読まれるため、案内はエージェントを介して行う。
- Linuxでは、ディストリビューションごとにパッケージマネージャーが異なるため、インストールガイドを案内する。ast-grepはnpm（`npm install --global @ast-grep/cli`）やpip（`pip install ast-grep-cli`）でも導入できる。
- ast-grepは短縮名の `sg` ではなく `ast-grep` を確認する。Linuxでは `sg` がshadow-utilsの同名コマンドと衝突し、ast-grep自体も0.45.0で `sg` を非推奨にしている。
- `jq --version` は `jq-1.6` のようにパッチ番号を省略する版があるため、省略時は0とみなして比べる。
- hook自体はjqを使わない。エージェントがJSONを扱うときの補助ツールとして確認する。
- ツールを追加する場合は、定義ファイルに1件加え、テストの期待値を更新する。

## トラブルシューティング

### ripgrepをインストールしたのに、バージョンが古いと案内される
VS Codeは独自のripgrepを同梱し、VS Codeから起動したプロセスではそのディレクトリがPATHに含まれることがある。同梱版が古い場合、ripgrepを別途インストールしていても、PATH上で先に見つかった同梱版が確認され、古いと案内される。

1. 実際に使われている `rg` の場所を確認する。
   - Windows：`where.exe rg`
   - macOS/Linux：`which -a rg`
2. 先頭のパスに `Microsoft VS Code` や `@vscode/ripgrep` が含まれていれば、VS Code同梱版である。次のいずれかで対処する。
   - VS Codeを更新する。同梱のripgrepも一緒に更新される。
   - 別途インストールしたripgrepのディレクトリを、PATH上でVS Code同梱版より前に置く。
3. VS Codeを再起動し、新しいセッションで再確認する。
