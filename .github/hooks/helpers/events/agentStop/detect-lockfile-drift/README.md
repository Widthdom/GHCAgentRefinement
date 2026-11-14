# detect-lockfile-drift hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
今回の依頼で依存の宣言を変えたマニフェストについて、同じディレクトリのロックファイルがHEADから変わっていなければ、ロックファイルを更新するか依存の変更を戻すよう差し戻す。

## 処理詳細
1. [lockfile-drift-definitions.json](./lockfile-drift-definitions.json) から、マニフェストの種類ごとのファイル名の正規表現（`filePattern`）、形式（`format`）、依存の宣言の場所（`json`・`toml` は `dependencyKeys`、`lines` は `linePatterns`）、ロックファイルと更新コマンド（`lockFiles`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（[read-transcript-events](../../../utilities/read-transcript-events/README.md)）。
3. ユーザーの最新の依頼以降について、編集系ツール（`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`）が書き込んだファイルのパスを、[edit-tool-changes](../../../utilities/edit-tool-changes/README.md) の規則で集める。`apply_patch` は `*** Add File:`・`*** Update File:`・`*** Move to:` のパスとする。
4. `git rev-parse --show-toplevel` でリポジトリルートを特定する。gitがない、またはリポジトリ外の場合は何もせず正常終了する。
5. リポジトリ内で、ファイル名が `filePattern` に一致するファイルについて、HEADの内容（`git show HEAD:<パス>`）と現在の内容から依存の宣言だけを取り出して比べる。
   - `json`・`toml`：ドット区切りのキー（`*` はその階層のすべてのキー）の値。
   - `lines`：`linePatterns` に一致した部分（連続する空白は1つにまとめる）の並び替えた一覧。
6. 依存の宣言が変わっていて、同じディレクトリにある `lockFiles` のロックファイルがすべてgit管理下で、`git diff --quiet HEAD` でHEADから変わっていなければ、ドリフトとする。
7. ドリフトがあれば、`<マニフェスト>: <ロックファイル> unchanged; run <コマンド>` の形式で最大10件列挙し、ロックファイルを更新するか依存の変更を戻すよう求める英語の理由とともに `{"decision":"block","reason":"..."}` を標準出力して差し戻す。

## 検出する内容

|種類|マニフェスト|依存の宣言|ロックファイルと更新コマンド|
|--|--|--|--|
|npm・Yarn・pnpm・Bun|`package.json`|`dependencies`・`devDependencies`・`optionalDependencies`・`peerDependencies`・`bundleDependencies`・`overrides`・`resolutions`・`pnpm.overrides`・`workspaces`|`package-lock.json`・`npm-shrinkwrap.json`（`npm install`）・`yarn.lock`（`yarn install`）・`pnpm-lock.yaml`（`pnpm install`）・`bun.lock`・`bun.lockb`（`bun install`）|
|Composer|`composer.json`|`require`・`require-dev`・`replace`・`conflict`・`provide`|`composer.lock`（`composer update --lock`）|
|uv・Poetry・PDM|`pyproject.toml`|`project.dependencies`・`project.optional-dependencies`・`dependency-groups`・`tool.poetry` の依存・`tool.uv.sources` など|`uv.lock`（`uv lock`）・`poetry.lock`（`poetry lock`）・`pdm.lock`（`pdm lock`）|
|Pipenv|`Pipfile`|`packages`・`dev-packages`・`requires`|`Pipfile.lock`（`pipenv lock`）|
|Cargo|`Cargo.toml`|`dependencies`・`dev-dependencies`・`build-dependencies`・`target.*` の依存・`workspace.dependencies`・`workspace.members`・`patch`|`Cargo.lock`（`cargo update --workspace`）|
|Go|`go.mod`|`require`・`replace` の行|`go.sum`（`go mod tidy`）|
|Bundler|`Gemfile`|`gem`・`source` の行|`Gemfile.lock`（`bundle install`）|
|NuGet|`*.csproj`・`*.fsproj`・`*.vbproj`|`<PackageReference>`・`<PackageVersion>` の要素|`packages.lock.json`（`dotnet restore --force-evaluate`）|

## 前提条件
- HEADと比べるため、gitとコミット済みのHEADが必要。

## 例外
- サブエージェントの停止は対象外。
- 依頼の区切りは [read-transcript-events](../../../utilities/read-transcript-events/README.md) の規則に従う。
- 依存の宣言以外の変更（`version`・`scripts`・整形など）は対象外とする。
- 同じディレクトリにロックファイルがない構成、HEADにないマニフェスト（新規作成）、読めないマニフェスト（JSON・TOMLの構文エラー）は対象外とする。
- ロックファイルのどれかがgit管理下にない（新たに作った）か、HEADから変わっていれば、更新済みとみなす。
- シェルでマニフェストを書き換えるコマンド（`npm install <パッケージ>`・`dotnet add package` など）は、ロックファイルも同時に更新するため対象外とする。

## 制約と限界
- 同じ依頼で差し戻すのは1回だけ。意図してロックファイルを変えない場合でも停止できるよう、2回目以降の応答は検査しない。
- ロックファイルの中身がマニフェストと整合しているかは見ず、HEADから変わったかだけを見る。今回の依頼より前からある未コミットの変更も、HEADとの差分として扱う。
- ロックファイルはマニフェストと同じディレクトリのものだけを見る。ワークスペースのルートにだけロックファイルがある構成や、`Directory.Packages.props` による中央管理は対象外。
- NuGetの `<PackageReference>` は1行の要素として照合するため、子要素の `<Version>` だけを変えた場合は検出しない。

## 背景
エージェントはマニフェストを直接編集して依存を追加・更新し、ロックファイルの再生成を忘れやすい。手元では動いても、CIの `npm ci`・`uv sync --locked`・`cargo build --locked`・`dotnet restore --locked-mode` が失敗するか、テストしていないバージョンが解決される。応答終了時にHEADとの差分で機械的に確かめる。

## 備考
- 状態ファイルは作らない。差し戻し済みかはトランスクリプトの差し戻しメッセージで判定する。
- 正規表現はPythonの `re` 構文で書く。テスト用に `--definitions-path` で定義ファイルを差し替えられる。
