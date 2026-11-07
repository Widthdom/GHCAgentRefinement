# block-native-newlines hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
Pythonで、改行を `\n` に固定せずにテキストを書き出す呼び出し（`newline=` のない標準出力・標準エラーの `reconfigure`、テキストモードの `open` など）を書き込む編集を拒否する。Windowsでは書き出すときに `\n` が `\r\n` に変わり、出力や書き込んだ内容がOSによって変わるため。

## 処理詳細
1. [newline-definitions.json](./newline-definitions.json) から、対象のファイルのパス（`includedFilePatterns`）、改行を変換する呼び出しの名前と正規表現（`translatingCalls`）、書き込みのモードの正規表現（`textWriteModePattern`）、`newline=` の指定の正規表現（`pinnedNewlinePattern`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）の引数を [extract-edit-tool-changes](../../../utilities/extract-edit-tool-changes/README.md) の規則で解析し、ファイルごとに書き込む内容と置換前の内容を特定する。作成（`create`）ではファイル全体、それ以外では書き込む内容（パッチでは追加行）を検査する。パッチによるファイルの削除は対象外とする。
3. cwdからの相対パス（`/` 区切り）が `includedFilePatterns` に一致するファイルについて、書き込む内容から `translatingCalls` の呼び出しを探す。文字列リテラルとコメントの中は除く。
4. 呼び出しの括弧の範囲（複数行にわたってもよい）の引数に `pinnedNewlinePattern` があれば除く。`requiresTextWriteMode` が `true` の呼び出しは、引数の文字列リテラルに `textWriteModePattern` に一致するもの（書き込みのモード）がなければ除く。
5. 呼び出しの先頭の行が、前後の空白を除いて既存の内容（置換前の内容、パッチの削除行、ファイルの現在の内容）にあれば除く。
6. 残った呼び出しのうち、このセッションで同じファイルをまだ拒否していないものがあれば、`newline='\n'` を指定するよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。理由には、ファイル（cwdからの相対パス、`/` 区切り）・種類・呼び出しの先頭の行を最大3件示す。

## 検出する内容

|種類|例|改行を固定した例|
|--|--|--|
|stdout/stderr reconfigure|`cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8')`・`sys.stderr.reconfigure(...)`|`reconfigure(encoding='utf-8', errors='replace', newline='\n')`|
|text-mode open for writing|`open(path, 'w')`・`open(path, mode='a')`・`open(path, 'r+')`・`io.open(...)`・`Path.open('x')`・`os.fdopen(fd, 'w')`・`gzip.open(path, 'wt')`|`open(path, 'w', encoding='utf-8', newline='\n')`|
|Path.write_text|`Path(path).write_text(text)`|`write_text(text, encoding='utf-8', newline='\n')`|
|TextIOWrapper|`io.TextIOWrapper(buffer, encoding='utf-8')`|`io.TextIOWrapper(buffer, encoding='utf-8', newline='\n')`|
|text-mode temporary file|`tempfile.NamedTemporaryFile(mode='w')`|`NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n')`|

## 例外
- `newline=''` など、`\n` 以外の値の指定も、改行を変換しない指定として認める。
- バイナリのモード（`'wb'` など）、読み込みのモード（`'r'`・モードの省略）、モードを変数で渡す呼び出しは対象外とする。
- 文字列リテラル（テストの入力として書いたコマンドなど）とコメントの中の呼び出しは対象外とする。
- 既存の行と同じ行は対象外とし、インデントの変更や周囲の編集で既存のコードを新たなものとみなさない。
- `.py`・`.pyi` 以外のファイルは対象外とする。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../../../events/preToolUse/block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じファイルにつき1回だけ。改行を変換しても問題がない呼び出しなど意図した編集は、同じ編集を再実行すれば通る。
- 書き込む内容だけを見るため、複数行の呼び出しの一部だけを書き換える編集では、括弧の範囲が書き込む内容の終わりまでになり、`newline=` が範囲の外にあれば誤って拒否しうる。
- 正規表現で検出するため、定義にない書き方（`codecs.open`、モードを変数で渡す `open` など）は見逃す。読み込み用の `TextIOWrapper` も、読み書きを区別できないため対象にする。

## 備考
- 状態にはファイルパスを保存せず、照合用のSHA-256だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-native-newlines/<セッションID>.sqlite3` に保存する。拒否しない編集では状態ファイルを作らない。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
- 規約は [hookの共通規約](../../../../../skills/create-hook/references/hook-conventions.md) の項目8に記載している。
