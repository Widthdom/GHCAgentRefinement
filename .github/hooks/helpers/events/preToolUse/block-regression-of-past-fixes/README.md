# block-regression-of-past-fixes hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理概要
バグ修正のコミットで入った行を、そのコミットを読まずに消す・書き換える編集を拒否する。`git show` でその行が入った理由を確かめるまで拒否し続け、修正の意図を保つよう求める。

## 処理詳細
1. [past-fix-definitions.json](./past-fix-definitions.json) から、修正コミットのメッセージに一致する正規表現（`fixCommitPatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）が編集系ツールなら、その引数から対象のファイルごとに、書き込む内容と置換前の内容を特定する。各ツールの引数は [edit-tool-changes](../../../utilities/edit-tool-changes/README.md) の規則で解析する。
3. 既存のファイルについて、編集で消える行の行番号を求める。書き込む内容に同じ行（前後の空白は無視する）が残る行と、単語の文字を含まない行（`}`・空行など）は除く。
   - `edit`・`str_replace`（置換）：現在のファイルで置換前の内容（`old_str`）が最初に現れる位置から、その行を特定する。
   - `create`（上書き）：現在のファイルの各行。
   - `apply_patch`：削除行（`-`）と同じ内容の、現在のファイルのすべての行。
4. 求めた行に `git blame --porcelain -L` を実行し、行を入れたコミットを特定する。まだコミットしていない行は除く。
5. 特定したコミットのメッセージ全体（件名と本文）を `git show -s` で読み、`fixCommitPatterns` のいずれかに一致するものを修正コミットとする。
6. 修正コミットのうち、このセッションでまだ読んでいないものを残す。読んだコミットは [record-viewed-commits](../../postToolUse/record-viewed-commits/README.md) hookが `git show`・`git log` の出力から記録したものを、[viewed-commits-state.py](../../../utilities/viewed-commits-state/viewed-commits-state.py) で読み出す。
7. 読んでいない修正コミット由来の行があれば拒否する。その際、ファイル・行番号・コミットID・件名・実行すべき `git show <コミットID>`（1ファイルにつき3コミットまで）と、コミットを読んで修正の意図を保つよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力する。

## 検出する内容

|種類|例|
|--|--|
|英語の修正コミット|`fix: ...`・`Fixes crash ...`・`Hotfix ...`・`bugfix: ...`・`... bug ...`・`Prevent regression ...`|
|issueを閉じるコミット|`closes #123`・`Resolved #45`|
|日本語の修正コミット|`修正`・`不具合`・`バグ`・`障害`・`回帰` を含むもの|

例：`fix: guard division by zero` で入った2行を消す次の編集を拒否する。

```diff
 def divide(a, b):
-    if b == 0:
-        raise ZeroDivisionError('b must not be zero')
     return a / b
```

## 前提条件
- `git` が必要。インストール状況は [check-required-tools](../../sessionStart/check-required-tools/README.md) hookが確認する。

## 例外
- 行を足すだけの編集（`insert` を含む）と、修正コミット由来の行を残す編集は対象外とする。
- 新規ファイル、git管理外のファイル、まだコミットしていない行は対象外とする。`git` がない、`git blame` が失敗した、または時間内に終わらない場合も検査しない。
- `#123` のようなissue番号だけのメッセージは対象外とする。GitHubのsquash mergeでは件名の末尾に必ず `(#123)` が付き、ほぼすべての行が対象になるため。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 読んだかどうかは、コマンドの出力にコミットの見出し行が現れたかで判定する。読んだ後に修正を消す判断は止めない。読んだうえで消すのは、正当な判断でありうるため。
- `apply_patch` では削除行の位置を特定せず、同じ内容の行をすべて対象にするため、修正コミットと無関係な同じ内容の行でも拒否しうる。
- `git blame` は行を最後に変えたコミットを返す。修正の後に整形やリネームだけのコミットで行が変わると、その行は修正由来と判定できない。
- 修正コミットはメッセージの正規表現で推定する。`誤字を修正` のような修正以外の意味でも一致し、キーワードのない修正コミットは検知できない。
- gitの実行は1回4秒、全体で7秒までとし、hookの `timeoutSec`（10秒）より前に打ち切る。多くのファイルを編集して期限を過ぎた場合、残りのファイルは検査しない。

## 背景
エージェントは、コードの見た目だけを手がかりに「不要な分岐」「冗長なチェック」を消しがちになる。しかし、その行は過去にバグを直すために足されたものかもしれない。リポジトリの履歴はその理由を知っているので、git blameで行の由来を調べ、巻き戻す前に経緯を読ませる。

## 備考
- 状態は読むだけで、書き込まない。読んだコミットの記録は `<一時フォルダー>/ghc-agent-refinement/hook-states/viewed-commits/<セッションID>.sqlite3` にある。
- 正規表現はPythonの `re` 構文で書き、大文字小文字を区別しない場合は `(?i)` を先頭に付ける。テスト用に `--definitions-path` で定義ファイルを差し替えられる。
- 不正な入力では異常終了する。Copilotはhookの異常終了をツール実行の拒否として扱う。
