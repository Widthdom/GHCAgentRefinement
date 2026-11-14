# detect-unrequested-changes hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
依頼にない整形・削除・移動や、依頼にも応答にも名前が出てこないファイルの変更を検出し、戻すか、必要な変更なら応答でファイルと理由を示すよう差し戻す。

## 処理詳細
1. [unrequested-change-definitions.json](./unrequested-change-definitions.json) から、対象外のファイルのパス（`excludedFilePatterns`）、整形を依頼したとみなす正規表現（`formattingRequestPatterns`）、削除・移動を依頼したとみなす正規表現（`removalRequestPatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（[read-transcript-events](../../../utilities/read-transcript-events/README.md)）。
3. ユーザーの最新の依頼以降について、依頼の文（割り込みの指示を含む）、応答の文（途中の応答を含む）、編集系ツール（`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`）の変更を集める。[read-transcript-events](../../../utilities/read-transcript-events/README.md) で開始と完了のイベントを対応させ、完了イベントの `success` が `true` の呼び出しだけを対象とする。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path` のファイルの変更とする（`str_replace_editor` の `view`・`undo_edit` は除く）。`old_str` と `new_str` があれば、空白だけが異なるかを判定する。
   - `apply_patch`：`*** Add File:`・`*** Update File:`・`*** Delete File:` ごとの変更とし、`*** Move to:` を移動とする。`*** Update File:` の削除行（`-`）と追加行（`+`）が空白だけ異なるかを判定する。
   - 各ツールの引数は [edit-tool-changes](../../../utilities/edit-tool-changes/README.md) の規則で解析する。
4. cwdからの相対パス（`/` 区切り）が `excludedFilePatterns` に一致しないファイルについて、次のいずれかに該当するものを検出する。
   - 依頼の文が `formattingRequestPatterns` に一致しないのに、空白（インデント・改行・行末の空白）だけを変えた。
   - 依頼の文が `removalRequestPatterns` に一致しないのに、`apply_patch` でファイルを削除・移動した。
   - 依頼の文にも応答の文にも、ファイル名がない。拡張子を除いた名前と親ディレクトリ名も、4文字以上なら言及とみなす。大文字小文字は区別しない。
5. 該当するファイルがあれば、cwd配下のファイルはcwdからの相対パス（`/` 区切り）で、ファイルごとに理由をまとめて最大10件を列挙し、依頼の範囲外の変更は戻し、必要な変更なら応答でファイルと理由を示すよう求める英語の理由とともに `{"decision":"block","reason":"..."}` を標準出力して差し戻す。

## 検出する内容

|種類|例|
|--|--|
|依頼にない整形|不具合の修正の依頼で、無関係な行のインデントや `f(a,b)` → `f(a, b)` を変えた|
|依頼にない削除・移動|依頼にない `*** Delete File:`・`*** Move to:`|
|言及のないファイルの変更|依頼にも応答にも名前が出てこないファイルを変えた|
|整形の依頼とみなす語|`整形`・`フォーマット`・`インデント`・`空白`・`改行`・`lint`・`format`・`indent`・`whitespace`・`prettier` など|
|削除・移動の依頼とみなす語|`削除`・`消して`・`リネーム`・`移動`・`整理`・`分割`・`統合`・`delete`・`remove`・`rename`・`move`・`clean up` など|

## 例外
- ロックファイル（`package-lock.json`・`yarn.lock`・`pnpm-lock.yaml`・`poetry.lock`・`uv.lock`・`Cargo.lock`・`go.sum` など）は対象外とする。
- 書き込む時点では判定せず、応答終了時に依頼の文と応答の文をそろえて判定する。途中の応答で言及したファイルも対象外とする。
- サブエージェントの停止は対象外。
- 依頼の区切りと依頼の文は [read-transcript-events](../../../utilities/read-transcript-events/README.md) の規則に従う。割り込みで追加された指示は依頼の文に含め、他のhookの差し戻しは含めない。
- 最新の依頼の編集だけを対象とする。
- 拒否・失敗した編集、未完了の編集、対応する開始イベントがない完了は変更とみなさない。ファイルの現在の有無では判定しないため、成功した削除・移動は引き続き検査する。
- シェルによる書き込み・削除（`rm`・`git mv` など）は対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../../preToolUse/block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 同じ依頼で差し戻すのは1回だけ。2回目以降の応答は検査しない。
- 依頼の範囲は文の意味では判定しない。言及したファイルの中の依頼にない変更や、「削除」などの語を含む依頼での無関係な削除は見逃す。

## 備考
- 依頼に含まれない変更（ついでの整形・リネーム・削除や、関係のないファイルの編集）を、ユーザーが気付かないまま受け入れる事故を防ぐためのhook。依頼の取りこぼしを検知する [detect-dropped-requirements](../detect-dropped-requirements/README.md) hookに対し、こちらは依頼にない変更を扱う。
- 言及のないファイルの変更を検出するのは、依頼にも応答にも出てこない変更は、ユーザーがレビューする機会がないため。テストやREADMEなど依頼に必要な変更でも、応答でファイル名を示せば、ユーザーが確認できる。
