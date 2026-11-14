# check-cross-agent-skill-wrappers hook README

## 発動条件

postToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` が成功した直後）

## 処理概要

skillの正本（`.github/skills`）かラッパー（`.agents/skills`・`.claude/skills`）の `SKILL.md` を編集した直後に、ラッパーの欠落や正本との食い違いを検出し、規約に沿って直すよう助言する。

## 処理詳細

1. [edit-tool-changes](../../../utilities/edit-tool-changes/README.md) で、ツール引数の `path`（`create` では `file_path`）、または `apply_patch` のパッチ本文の `*** Update File:` などの行から、編集されたファイルを特定する。
2. 編集されたファイルが、skillの正本 `.github/skills/<name>/SKILL.md` か、ラッパー `.agents/skills/<name>/SKILL.md`・`.claude/skills/<name>/SKILL.md` であれば、そのskillの3ファイルを読み、次の問題を検出する。
   - ラッパーがない
   - ラッパーの `name`・`description` が正本と異なる（引用符も含めて比べる）
   - ラッパーの本文が、正本を参照する定型文と異なる
   - 正本の `name` がフォルダ名と異なる
   - 正本がないのにラッパーが残っている
3. 問題があれば、[skillの共通規約](../../../../../skills/create-skill/references/skill-conventions.md) に沿って直すよう促す英語の助言を `additionalContext` として渡す。

## 例外

- 正本とラッパーがどちらもないskillは、skill自体を削除したものとして扱う。
- 補助ファイル（`scripts/` や `README.md` など）の編集では何もしない。

## 制約と限界

- skillを新規作成した直後や、正本を更新してラッパーを順に直している途中でも問題として検出する。助言には、更新の途中なら最後まで終えるよう書いている。
- 同じセッションで同じskillの同じ問題は1回だけ通知する。新しい問題が加わった場合は、そのskillの問題をまとめて通知する。解消した後で再び崩れた場合は、改めて通知する。
- ターミナルからのファイル操作（`rm`・`mv`・`sed -i`・リダイレクトでの書き込みなど）は、編集ツールではないため検出できない。

## 備考

- 正本の `name`・`description` を変えたときにラッパーへの反映を忘れる、ラッパーに手順を書き足す、といった規約違反を編集の直後に知らせるためのhook。
- リポジトリルートは、編集されたファイルのパスから求める。状態にはルートとskill名、検出した問題を保存する。問題がない通常の編集では状態ファイルを作らない。
