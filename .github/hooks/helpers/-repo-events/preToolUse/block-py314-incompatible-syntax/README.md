# block-py314-incompatible-syntax hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
編集後のPythonファイルがPython 3.14でコンパイルできない場合（3.15以降で追加された文法、Python 2の文法、構文エラーなど）、その編集を拒否する。

## 処理詳細
1. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から書き込み先のファイルごとに編集前と編集後のファイル全体の内容を、[edit-tool-changes](../../../utilities/edit-tool-changes/README.md) の規則で求める。拡張子が `.py`・`.pyi`・`.pyw`（大文字小文字を区別しない）でないファイルは対象外とする。
2. 編集後の内容を、`ast.parse`（`feature_version=(3, 14)`）で構文解析し、`compile` でコンパイルする。`SyntaxError`（`IndentationError`・`TabError` を含む）になったファイルを拒否の候補とする。警告（`SyntaxWarning` など）は対象外とする。
3. 候補のうち、編集前の内容がコンパイルできるか、まだ存在しないファイルについて、Python 3.14でコンパイルできるよう直すことを求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。理由には、cwd配下のファイルをcwdからの相対パス（`/` 区切り）で、エラーの行番号と内容とともに最大3件示す。

## 検出する内容

|種類|例|
|--|--|
|Python 3.15以降で追加された文法|hookを3.15以降で実行した場合、`feature_version` で3.14の文法として判定する|
|Python 2の文法|`print "x"`・`exec "x"`・`raise E, "x"`・`0777`・`` `x` ``・`<>`|
|コンパイル時のエラー|関数外の `return`・`await`、モジュールレベルの `nonlocal` など|
|構文エラー|閉じていない括弧、インデントの誤りなど|

## 前提条件
- hookをPython 3.14以降で実行する。対象ファイルの判定時に3.14未満なら、3.14の文法（テンプレート文字列や括弧なしの複数例外など）を誤って拒否しないよう、異常終了する。

## 例外
- 編集前の内容が既にコンパイルできないファイルの編集は、修正途中の場合もあるため拒否しない。
- 編集後の内容を特定できない編集は、ツール自体が失敗するか判定できないため拒否しない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../../../events/preToolUse/block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は回数を限らない。コンパイルできないことを意図したPythonファイル（テストの入力など）を新たに作る場合は、拡張子を変えるか、テスト内で文字列として扱う必要がある。
- `feature_version` による新しい文法の判定はCPythonの文法の実装に依存し、hookを3.15以降で実行した場合、一部の新しい文法を見逃す可能性がある。
- Python 2の `except E, ex:` は、3.14では括弧なしの複数例外（PEP 758）として正しい文法になるため検出しない。
- 標準ライブラリAPIの有無（3.15以降で追加された関数など）は文法ではないため検出しない。
- 編集後の内容の求め方による限界（CRLFのファイルの置換、パッチの適用位置）は [edit-tool-changes](../../../utilities/edit-tool-changes/README.md) を参照。

## 備考
- このリポジトリのPythonスクリプトはPython 3.14を最低対応とするため（[hookの共通規約](../../../../../skills/create-hook/references/hook-conventions.md) 項目6）、3.14で動かない文法を書き込む前に防ぐためのhook。
- 状態は保存しない。
- hookの異常終了（不正な入力や3.14未満での実行）は、Copilotが編集の拒否として扱う。
