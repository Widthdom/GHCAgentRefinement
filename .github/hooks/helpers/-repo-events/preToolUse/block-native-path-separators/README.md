# block-native-path-separators hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
OS依存の区切り（Windowsでは `\`）で相対パスを返すAPIの呼び出しを、同じ行で `/` 区切りに変換せずに書き込む編集を拒否する。

## 処理詳細
1. [path-separator-definitions.json](./path-separator-definitions.json) から、対象外のファイルのパス（`excludedFilePatterns`）、OS依存の区切りのパスを返す呼び出しの正規表現（`nativePathPatterns`）、`/` 区切りへの変換とみなす正規表現（`normalizationPatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から対象のファイルごとに書き込む内容と置換前の内容を特定する。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path`・`command`・`old_str` を除く引数の文字列を書き込む内容、`old_str` を置換前の内容とする（`str_replace_editor` の `view` は除く）。
   - `apply_patch`：`*** Add File:`・`*** Update File:` ごとのパッチの追加行（`+`）を書き込む内容、削除行（`-`）を置換前の内容とする。`*** Delete File:` は対象外とする。
3. cwdからの相対パス（`/` 区切り）が `excludedFilePatterns` に一致しないファイルについて、書き込む内容のうち `nativePathPatterns` に一致し、同じ行で `normalizationPatterns` に一致しない行を拒否の候補とする。ただし、前後の空白を除いて同じ行が既存の内容（置換前の内容、パッチの削除行、ファイルの現在の内容）にあれば除く。
4. 候補のうち、このセッションで同じファイルをまだ拒否していないものがあれば、パスを `/` 区切りに変換するよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。理由には、cwd配下のファイルをcwdからの相対パス（`/` 区切り）で示す。

## 検出する内容

|種類|例|`/` 区切りへの変換の例|
|--|--|--|
|Python relative path|`os.path.relpath(path, cwd)`・`path.relative_to(root)`|`.replace(os.sep, '/')`・`.as_posix()`・`posixpath.relpath(...)`|
|Node.js relative path|`path.relative(root, file)`|`.split(path.sep).join('/')`・`.replace(/\\/g, '/')`・`path.posix.relative(...)`|
|.NET relative path|`Path.GetRelativePath(root, file)`・`[System.IO.Path]::GetRelativePath(...)`|`.Replace(Path.DirectorySeparatorChar, '/')`・`.Replace("\\", "/")`|
|Go relative path|`filepath.Rel(root, file)`|`filepath.ToSlash(...)`|
|Java relative path|`root.relativize(file)`|`.replace(File.separatorChar, '/')`・`separatorsToUnix(...)`|
|PowerShell relative path|`Resolve-Path $file -Relative`|`-replace '\\', '/'`|
|対象外のファイル|`*.md`・`*.markdown`・`*.rst`・`*.txt`・`*.adoc`||

## 例外
- 既存の行と同じ行は対象外とし、インデントの変更や周囲の編集で既存のコードを新たなものとみなさない。
- ドキュメントのコード例で誤検知しないよう、文書のファイルは対象外とする。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../../../events/preToolUse/block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じファイルにつき1回だけ。ファイルシステムのAPIに渡すだけの場合や、別の行で変換している場合でも、同じ編集を再実行すれば通るようにする。
- 変換を同じ行に限るのは、変換の有無を書き込む内容全体で判定すると、ファイル内のどこか1か所の変換で、変換していない別の箇所を見逃すため。
- 正規表現で検出するため、定義にない形式（`os.path.join` の結果の表示、`str(Path(...))`、テストの期待値に書いた `/` 区切りのパスなど）は見逃す。

## 備考
- 相対パスを返すAPIは、Windowsでは `\`、それ以外では `/` で区切ったパスを返す。そのパスを表示・保存・文字列比較すると、開発したOSではテストが通り、別のOSで失敗する。この事故を、書き込む前に防ぐためのhook。相対パスは表示や照合に使われることが多いため、これを返すAPIを対象にする。
- 状態にはファイルパスを保存せず、照合用のSHA-256だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-native-path-separators/<セッションID>.sqlite3` に保存する。拒否しない編集では状態ファイルを作らない。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
