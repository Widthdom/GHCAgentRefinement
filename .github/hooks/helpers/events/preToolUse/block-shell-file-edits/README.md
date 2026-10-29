# block-shell-file-edits hook README

## 発動条件

preToolUse（bashまたはpowershellツールの実行前）

## 処理詳細

1. 並列に呼び出すツール（`toolCalls`）のうちbash・powershellのコマンドから、ファイルへの書き込み先を抜き出す。
   - bash：リダイレクト（`>`・`>>`・`>|`・`&>`・`2>` など）、`sed -i`・`sed --in-place`、`perl -i`・`perl -pi`、`tee`
   - powershell：リダイレクト（`>`・`>>`・`2>` など）、`Set-Content`・`Add-Content`・`Clear-Content`・`Out-File`・`Tee-Object` とその別名（`sc`・`ac`・`clc`・`tee`）、`[IO.File]::WriteAll*`・`AppendAll*`、powershellから呼ぶ `sed -i`・`perl -i`
2. 書き込み先のうち、既存のファイルで、gitの管理対象（`git ls-files` に出るもの）を探す。
3. 該当するファイルがあれば、ファイル編集ツールを使うよう求める英語の理由とともに、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 判定の範囲

### 許可する書き込み
- git管理外のファイル（新規作成、未追跡のファイル、一時フォルダー、`/dev/null`・`$null` など）
- `2>&1` などの記述子の複製
- ヒアドキュメント・ヒア文字列の本文、引用符の中、コメントに書かれた `>` など
- gitがない場合やリポジトリ外のファイル（判定できないため）。gitの導入は [check-git](../../sessionStart/check-git/README.md) hookが案内する。

### 書き込み先の表記の扱い
- コマンド内の `cd`・`pushd`・`Set-Location` で移動先が1つの文字列で示された場合は、後続のコマンドの基準をその移動先にする。サブシェルの範囲は区別しない。
- `*`・`?`・`[` を含む表記はglobとして展開する。
- 変数やコマンド置換（`$`・バッククォート）を含む表記は、展開後の値がわからないため対象外とする。
- 書き込み先を絞り込まず、`sed`・`Set-Content` などのオプション以外の引数をすべて候補とする（スクリプトや値も含む）。git管理ファイルと一致するものだけが拒否の対象になるため、値が偶然ファイル名と同じ場合に限って誤って拒否する。
- 大文字小文字を区別しないファイルシステムでも照合できるよう、`git ls-files` の一覧と `os.path.normcase` で比べる。

### 検出できない書き込み
字句解析は簡易的なもので、bashは `shlex`、powershellは独自の処理で行う。次の書き込みは検出できない。
- `python -c`・`awk` などのスクリプト内部での書き込み、`cp`・`mv`・`dd` などでの上書き
- `xargs` などで標準入力から渡されるファイル
- `New-Item -Force`・`Copy-Item` での上書き、`[IO.File]` 以外の.NET API

## 備考

- 特別なプロンプト指定は不要。
- シェルでファイルを書き換えると、ファイル編集ツールを対象にした [require-read-before-edit](../require-read-before-edit/README.md)・[preserve-line-endings](../../postToolUse/preserve-line-endings/README.md)・[check-cross-agent-skill-wrappers](../../postToolUse/check-cross-agent-skill-wrappers/README.md) などのhookが働かない。それらの前提を保つためのhook。
- 拒否は毎回行う。編集ツールで変更できない事情（バイナリファイルなど）があれば、ユーザーに理由を説明して判断を仰ぐよう理由に書いている。
- 不正な入力では異常終了する。Copilotはhookの異常終了をツール実行の拒否として扱う。
