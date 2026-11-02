# block-unverified-dependencies hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
依存ファイルやPythonのimportに新たに加える依存パッケージのうち、手元の情報でもレジストリへの問い合わせの記録でも実在を確かめられないものがあれば、編集を拒否する。

## 処理詳細
1. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から書き込む内容と対象のファイルを特定する。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path`・`command`・置換前の内容（`old_str`）を除く引数の文字列を書き込む内容とする（`str_replace_editor` の `view` は除く）。
   - `apply_patch`：パッチの追加行（`+`）を書き込む内容、削除行（`-`）を置換前の内容とし、`*** Add File:`・`*** Update File:` の行から対象を特定する。
2. 対象のファイルの種類ごとに、書き込む内容から依存の名前（NuGetではバージョンも）を取り出す（[検出する内容](#検出する内容)）。
3. 既存の内容（置換前の内容、ファイルの現在の内容）に同じ名前・同じバージョンの依存があれば、移動や周辺の編集とみなして除く。名前が既にあり、バージョンだけを変える場合は、バージョンだけを照合する。
4. [record-verified-packages](../../postToolUse/record-verified-packages/README.md) hookが、このセッションで実在を確かめたと記録した名前は通す。
5. 残りの依存を、ネットワークを使わずに手元の情報と照合する（[照合先](#照合先)）。どれかに見つかれば通す。
6. 見つからない依存を最大10件まで、綴りの近い既知の名前（`difflib` の類似度0.85以上）とともに列挙する。レジストリの検索で確かめてから再実行し、確かめるためにインストールしないよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 検出する内容
|種類|対象のファイル|取り出す内容|
|--|--|--|
|NuGet|`*.csproj`・`*.fsproj`・`*.vbproj`・`*.props`・`*.targets`|`<PackageReference Include="..." Version="...">`・`<PackageVersion Include="..." Version="...">`|
|PyPI|`requirements*.txt`・`requirements*.in`・`requirements/*.txt`|各行の配布名（オプション・URL・パスの行は除く）|
|PyPI|`pyproject.toml`|バージョン指定付きの配列の要素（`"httpx>=0.27",`）、`dependencies = [...]` などの1行の配列の要素、Poetryの `name = "^1.2"`・`name = { version = ... }`|
|npm|`package.json`|値がバージョンの形の `"name": "^1.2.3"`（`version`・`engines` の `node` などのキーは除く）|
|Pythonのimport|`*.py`・`*.pyi`|`import x`・`from x import y` の最上位の名前（相対importは除く）|

## 照合先
|種類|照合先|
|--|--|
|共通|リポジトリ内の他の依存ファイルに既にある名前（git管理下と無視されていない未追跡のファイル）|
|NuGet|グローバルパッケージフォルダー（`NUGET_PACKAGES`、なければ `~/.nuget/packages`）、各プロジェクトの `obj/project.assets.json`。バージョンはグローバルパッケージフォルダーのバージョンのフォルダー|
|PyPI|hookを動かすPythonとリポジトリ直下の仮想環境（`.venv` など）の `*.dist-info`・`*.egg-info`、`uv.lock`・`poetry.lock`・`pdm.lock`・`Pipfile.lock`|
|npm|依存ファイルのディレクトリからリポジトリのルートまでの `node_modules`、`package-lock.json`・`npm-shrinkwrap.json`・`yarn.lock`・`pnpm-lock.yaml`・`bun.lock`|
|Pythonのimport|標準ライブラリ（`sys.stdlib_module_names`）、リポジトリ内のモジュール・ディレクトリの名前、hookを動かすPythonとリポジトリ直下の仮想環境にインストール済みのモジュール|

## 前提条件
- 特別なプロンプト指定は不要。
- 確認の記録は [record-verified-packages](../../postToolUse/record-verified-packages/README.md) hookが書き込む。状態は [verified-packages-state.py](../../../utilities/verified-packages-state.py) で共有し、本hookは読み取りだけを行う。
- gitがない、またはリポジトリ外の場合は、ファイルの一覧の代わりにcwd配下を歩く（`node_modules`・`.venv`・`bin`・`obj` などは除く）。

## 例外
- バージョンが範囲・ワイルドカード・MSBuildのプロパティ（`[4.0,5.0)`・`4.*`・`$(Version)`）のNuGetの依存は、バージョンを照合しない。
- バージョンの照合はNuGetだけで行う。PyPIとnpmは、手元にない別のバージョンの指定が普通にあるため、名前だけを照合する。
- このセッションで実在を確かめた名前は、バージョンを照合しない。
- C#の `using` は、名前空間とパッケージが1対1に対応しないため対象外とする。JavaScript・TypeScriptのimportも対象外とする。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は、実在を確かめるまで毎回繰り返す。レジストリの検索という確かめる手段があるため、1回だけの拒否にはしない。レジストリに届かない環境では、エージェントからユーザーに追加したい依存を伝え、ユーザー自身が編集する。
- ネットワークを使わないため、存在しないことは証明できない。手元にないだけの正しい依存も、初めて追加するときは拒否する。
- 実在を確かめるだけで、安全かどうかは判定しない。正しい名前で公開された悪意のあるパッケージは見逃す。
- 依存ファイルの構造は解析せず、行の形で判定する。`pyproject.toml` のバージョン指定のない複数行の配列の要素（`"httpx",`）は見逃す。
- リポジトリ内に同じ名前のディレクトリやモジュールがあれば、Pythonのimportは通す。

## 備考
- LLMが書く依存の名前は、もっともらしい架空の名前になることがある。架空の名前はビルドの失敗で気づけるが、その名前で悪意のあるパッケージが先に登録されていると、ビルドが通ってしまう（slopsquatting）。この事故を、依存を書き込む前に防ぐためのhook。
- 拒否理由では、確かめるために `pip install` などでインストールしないよう求める。インストールすること自体が、悪意のあるパッケージを実行するきっかけになるため。
- hookの異常終了（不正な入力）は、Copilotが編集の拒否として扱う。
