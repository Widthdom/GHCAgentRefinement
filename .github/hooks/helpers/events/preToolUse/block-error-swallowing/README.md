# block-error-swallowing hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
空の例外ハンドラーやコマンドの失敗の無視、診断の抑止など、エラーを握りつぶすコードを新たに書き込む編集を拒否し、原因を直すかエラーを明示的に扱うよう求める。

## 処理詳細
1. [error-swallowing-definitions.json](./error-swallowing-definitions.json) から、対象外のファイルのパス（`excludedFilePatterns`）と、エラーを握りつぶすコードの正規表現（`swallowingPatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から対象のファイルごとに書き込む内容と置換前の内容を特定する。
   - `edit`・`str_replace_editor`：置換前の内容（`old_str`）がファイルにあれば、置換を適用した後のファイル全体。なければ `path`・`command`・`old_str` を除く引数の文字列。
   - 各ツールの引数は [edit-tool-changes](../../../utilities/edit-tool-changes/README.md) の規則で解析する。
3. cwdからの相対パス（`/` 区切り）が `excludedFilePatterns` に一致しないファイルについて、書き込む内容に `swallowingPatterns` に一致する箇所があれば拒否の候補とする。ただし、各行の前後の空白を除いて同じ箇所が既存の内容（置換前の内容、パッチの削除行、ファイルの現在の内容）にあれば除く。
4. 候補のうち、このセッションで同じファイルをまだ拒否していないものがあれば、原因を直すかエラーを明示的に扱うよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 検出する内容

|種類|例|
|--|--|
|空のPython例外ハンドラー|`except:` / `pass`・`except Exception: ...`・`except ValueError: pass`|
|Pythonの例外の抑止|`contextlib.suppress(Exception)`|
|空のcatchブロック|`catch (e) {}`・`catch { // ignore }`・`catch (IOException e) { /* nothing */ }`|
|空のPromiseのcatch|`.catch(() => {})`・`.catch((e) => null)`|
|Goのエラーの破棄|`_ = err`・`if err != nil { }`|
|コマンドの失敗の無視|`make || true`・`rm -rf out || :`・`set +e`|
|PowerShellのエラーの無視|`-ErrorAction SilentlyContinue`・`$ErrorActionPreference = 'Ignore'`|
|診断の抑止|`# type: ignore`・`# noqa`・`# pylint: disable`・`// @ts-ignore`・`eslint-disable`・`#pragma warning disable`・`@SuppressWarnings`・`//nolint`・`#[allow(`|

## 例外
- `2>/dev/null` は対象外とする。標準エラーを隠すだけで終了コードは伝わり、`command -v` などの正当な用途が多いため。
- `const enabled = options.flag || true;`・`return a || true;`・`if (a || true)`・`f(a || true)` のように、行頭の代入（宣言の語や型に続く左辺と、前後に空白がある `=`）・`return`・`if`・`while` の条件・関数の引数の中にある `|| true` は、シェル以外の言語の式とみなして対象外とする。シェルの `X=$(cmd || true)` と、`[ "$x" = y ] && cmd || true`・`test $x = y && cmd || true` の比較は対象とする。
- ドキュメントのコード例で誤検知しないよう、文書のファイル（`*.md`・`*.markdown`・`*.rst`・`*.txt`・`*.adoc`）は対象外とする。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 拒否は同じファイルにつき1回だけ。後片付けで失敗しても問題がない場合や、ユーザーの依頼による場合でも、同じ編集を再実行すれば通るようにする。拒否の理由では、無視する理由をコメントで残すよう求める。
- 正規表現で検出するため、定義にない形式（ログだけ出して処理を続けるハンドラーや、戻り値の無視など）は見逃す。

## 備考
- エラーや警告が出たとき、エージェントが原因を直さずに、例外を握りつぶしたり、コマンドの失敗や診断を黙らせたりして「動くようにした」とする事故を、書き込む前に防ぐためのhook。テストを弱めて成功させる抜け道を塞ぐ [block-test-weakening](../block-test-weakening/README.md) hookと同じ考え方を、実装側に広げたもの。
- 置換を適用した後のファイル全体と比べるのは、`except OSError:` の次の行の `raise` だけを `pass` に置き換える編集のように、書き込む内容だけでは握りつぶしと分からない編集を検出するため。インデントの変更や周囲の編集で、既存の握りつぶしを新たなものとみなさない。
- 状態にはファイルパスを保存せず、照合用のSHA-256だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-error-swallowing/<セッションID>.sqlite3` に保存する。拒否しない編集では状態ファイルを作らない。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
