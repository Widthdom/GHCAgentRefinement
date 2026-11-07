# block-exclusion-escapes hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
テスト・静的検査・コンパイル・カバレッジの除外設定に、git管理下の既存のコードやテストを加える編集を拒否し、除外して隠すのではなく直すよう求める。

## 処理詳細
1. [exclusion-definitions.json](./exclusion-definitions.json) から、行ごとに除外を書く無視ファイルの名前（`ignoreFiles`）と、設定ファイルの種類ごとの名前の正規表現（`filePattern`）・除外の値を直接取り出す正規表現（`valuePatterns`、1つのグループ）・除外の値を代入するキーの正規表現（`keywordPatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から対象のファイルごとに編集前と編集後の内容を求める。
   - `create`：`file_text` を編集後の内容とする。
   - `edit`・`str_replace_editor`：現在の内容の `old_str` を `new_str` に1回置き換える。`insert`（`command` が `insert`）は `insert_line` 行目の後に `new_str` を挿入する。`str_replace_editor` の `view`・`undo_edit` は除く。
   - `apply_patch`：`*** Add File:` は空のファイルから、`*** Update File:` は現在の内容にハンクを適用して求める。`*** Delete File:` は対象外とする。
3. ファイル名が `ignoreFiles` か `filePattern` に一致するファイルについて、編集前と編集後の内容からそれぞれ除外の指定を取り出し、編集後にだけあるもの（追加した除外）を求める。
   - 無視ファイル：空行・コメント（`#`）・否定（`!`）を除く各行。
   - `valuePatterns`：一致したグループ。
   - `keywordPatterns`：キーの直後が代入（`=`・`:`）なら、その値。値が括弧で始まれば対応する閉じ括弧まで、引用符で始まればその文字列、それ以外は行末までと続くインデントされた行（INIの継続行・YAMLのリスト）。値に引用符があれば引用符の中の文字列、なければ空白・カンマで区切った語を指定とする。
   - 指定は `/` 区切りにそろえ、先頭の `./`・`/`・`**/`・`<rootDir>`・`^`、末尾の `/**`・`/*`・`/`・`$`、pytestの `::<テスト名>`、正規表現のエスケープ（`\.`）を除く。
4. 追加した除外を、設定ファイルのディレクトリで `git ls-files` したファイル（そのディレクトリからの相対パス）と照合する。globはパス全体（`**/` が0階層に一致する場合も含む）か、`/` を含まなければパスの各要素と照合する。globでなければ、パスの先頭・途中のディレクトリやファイル名として一致するかを見る。
5. 一致したファイルがあり、このセッションで同じ設定ファイルをまだ拒否していなければ、除外の指定と一致したファイルを最大3件示し、除外せずに直すよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 検出する内容

|種類|設定ファイル|除外の指定の例|
|--|--|--|
|Pythonのテスト・静的検査|`pytest.ini`・`pyproject.toml`・`setup.cfg`・`tox.ini`・`.coveragerc`・`conftest.py`・`.flake8`・`mypy.ini`・`.pylintrc`・`ruff.toml`|`--ignore=`・`--ignore-glob=`・`--deselect`・`norecursedirs`・`collect_ignore`・`omit`・`exclude`・`extend-exclude`・`ignore-paths`・`ignore-patterns`|
|JavaScript・TypeScript|`package.json`・`tsconfig*.json`・`jsconfig.json`・`jest`・`vitest`・`vite`・`playwright`・`eslint`・`karma` の設定・`.eslintrc*`・`.mocharc*`・`.c8rc`・`.nycrc`|`testPathIgnorePatterns`・`modulePathIgnorePatterns`・`coveragePathIgnorePatterns`・`watchPathIgnorePatterns`・`exclude`・`ignores`・`ignorePatterns`・`testIgnore`|
|.NET|`*.csproj`・`*.fsproj`・`*.vbproj`・`Directory.Build.props`・`Directory.Build.targets`・`*.runsettings`|`<Compile Remove="...">`（`Content`・`None`・`EmbeddedResource` も）・`<ExcludeByFile>`・`<Exclude>`|
|Rust|`Cargo.toml`|`exclude`|
|カバレッジサービス|`codecov.yml`・`.codecov.yml`・`sonar-project.properties`|`ignore`・`sonar.exclusions`・`sonar.test.exclusions`・`sonar.coverage.exclusions`・`sonar.cpd.exclusions`|
|無視ファイル|`.eslintignore`・`.prettierignore`・`.stylelintignore`・`.markdownlintignore`・`.jshintignore`・`.semgrepignore`|各行|

例：次の編集を拒否する（`tests/test_calc.py` がgit管理下にある場合）。

```diff
-addopts = "-q"
+addopts = "-q --ignore=tests/test_calc.py"
```

## 前提条件
- git管理下のファイルを `git ls-files` で調べる。gitがない場合や、設定ファイルのディレクトリがgit管理下でない場合は検査しない。
- Python 3.14以降の標準の `sqlite3` を使うため、SQLite自体のインストールは不要（公式Windows版Pythonにも同梱）。

## 例外
- git管理下のファイルに一致しない除外（`node_modules`・`dist`・`.venv` などの生成物）は対象外とする。
- 編集前からある除外、除外の削除、否定（`!src/app.ts`）、コメントは対象外とする。
- キーの直後が代入でない場合（コメントの中の `exclude` など）は対象外とする。
- `.gitignore` は対象外とする。git管理下のファイルは無視の指定に関係なく追跡され続け、テストや静的検査からは隠れないため。
- 編集後の内容を特定できない編集（`old_str` が見つからないなど）は、ツール自体が失敗するため検査しない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じ設定ファイルにつきセッションで1回だけ。ユーザーが依頼した除外や生成ファイルの除外など意図した編集は、同じ編集を再実行すれば通る。
- どの用途の除外かは区別しない。パッケージに含めないための `exclude`（`[tool.setuptools.packages.find]` など）や、ビルド用の `tsconfig` の `exclude` でも、git管理下のファイルに一致すれば拒否する。
- 除外の指定は正規表現と簡易的な字句解析で取り出すため、`collect_ignore.append(...)` のような代入以外の書き方や、変数を介した指定は検出しない。
- Jestなどの正規表現による指定は、エスケープを外したパスとして照合するため、複雑な正規表現は一致を判定できない。

## 背景
失敗するテストや警告の出るファイルを、直さずに除外設定へ加えるとテストランナー・静的検査・カバレッジから見えなくなり、失敗が消えたように見える。テストの中身を書き換える [block-test-weakening](../block-test-weakening/README.md) hookでは防げない、設定ファイルからの抜け道を塞ぐ。

## 備考
- 状態にはパスを保存せず、照合用のSHA-256だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-exclusion-escapes/<セッションID>.sqlite3` に保存する。拒否しない編集では状態ファイルを作らない。
- 正規表現はPythonの `re` 構文で書き、大文字小文字を区別しない場合は `(?i)` を先頭に付ける。テスト用に `--definitions-path` で定義ファイルを差し替えられる。
- 不正な入力では異常終了する。Copilotはhookの異常終了をツール実行の拒否として扱う。
