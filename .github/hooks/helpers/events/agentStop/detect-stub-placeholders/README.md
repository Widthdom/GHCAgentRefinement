# detect-stub-placeholders hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
今回の依頼で書き込み、最終的なファイルに残っている仮実装（`NotImplementedError`・`TODO` など）を、最終応答で触れていなければ検出し、実装するか残す理由を明記するよう差し戻す。

## 処理詳細
1. [stub-placeholder-definitions.json](./stub-placeholder-definitions.json) から、対象外のファイルのパス（`excludedFilePatterns`）、仮実装の正規表現（`placeholderPatterns`）、応答で未実装に触れているとみなす正規表現（`disclosurePatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（Copilotはトランスクリプトへ非同期に追記するため、発動直後は最終応答が未書き込みのことがある）。
3. ユーザーの最新の依頼以降について、最終応答と、編集系ツール（`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`）の書き込む内容と置換前の内容を集める。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path`・`command`・`old_str` を除く引数の文字列を書き込む内容、`old_str` を置換前の内容とする（`str_replace_editor` の `view`・`undo_edit` は除く）。
   - `apply_patch`：`*** Add File:`・`*** Update File:` ごとのパッチの追加行（`+`）を書き込む内容、削除行（`-`）を置換前の内容とする。
   - 各ツールの引数は [extract-edit-tool-changes.py](../../../utilities/extract-edit-tool-changes/extract-edit-tool-changes.py) で解析する。
4. 最終応答が `disclosurePatterns` に一致する（「未実装」「スタブ」「TODO」「not implemented」などに触れている）場合は終了する。
5. cwdからの相対パス（`/` 区切り）が `excludedFilePatterns` に一致しないファイルについて、書き込む内容のうち `placeholderPatterns` に一致する行を探す。前後の空白を除いて同じ行が置換前の内容にあるもの（元からあるもの）と、ファイルの現在の内容にないもの（後の編集で消えたもの）は除く。同じファイルの同じ行は、複数の種類に一致しても1件にまとめる。この照合は [find-introduced-lines-by-regex.py](../../../utilities/find-introduced-lines-by-regex/find-introduced-lines-by-regex.py) で行う。
6. 該当する行があれば、cwd配下のファイルはcwdからの相対パス（`/` 区切り）で最大10件を列挙し、実装するか、残す箇所と理由を応答に明記するよう求める英語の理由とともに `{"decision":"block","reason":"..."}` を標準出力して差し戻す。

## 検出する内容

|種類|例|
|--|--|
|NotImplementedException|`throw new NotImplementedException();`|
|NotImplementedError|`raise NotImplementedError`|
|未実装を示す例外|`throw new Error('Not implemented')`・`throw new UnsupportedOperationException("TODO")`|
|未実装を示すpanic|`panic("not implemented")`|
|未実装のマクロ・関数|`todo!()`・`unimplemented!()`・`TODO()`（Kotlin）|
|TODOコメント|`// TODO`・`# TODO`・`/* TBD */`・`-- TODO`|
|実装の先送りを示すコメント|`// 後で実装`・`# 未実装`・`/* implement later */`・`// stub implementation`|
|対象外のファイル|`*.md`・`*.markdown`・`*.rst`・`*.txt`・`*.adoc`・`*.json`|

## 前提条件
- 特別なプロンプト指定は不要。

## 例外
- 編集の途中で仮実装を置くのは正当な手順のため、書き込む時点では拒否せず、応答終了時に最終的なファイルに残っているものだけを検出する。
- `FIXME` は対象外とする。[add-fixme-comment](../../../../../skills/add-fixme-comment/SKILL.md) skillが依頼範囲外の不具合の目印として意図的に書き込むため。
- サブエージェントの停止は対象外。
- hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
- 最新の依頼の編集だけを対象とする。それより前の依頼の編集やユーザーの変更で入った仮実装は検出しない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../../preToolUse/block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- 抽象メソッドの `raise NotImplementedError` など意図した仮実装でも、応答で触れていなければ差し戻す。応答で残す箇所と理由を示せば停止できる。
- 同じ依頼で差し戻すのは1回だけ。2回目以降の応答は検査しない。
- 正規表現で検出するため、定義にない形式（空の本体、固定値を返すだけの本体、Pythonの `pass` など）は見逃す。文字列リテラル内の一致も検出する。

## 備考
- エージェントが仮実装を残したまま、それに触れずに作業を完了として報告する事故を防ぐためのhook。既存コードの省略による消失を防ぐ [block-elided-rewrites](../../preToolUse/block-elided-rewrites/README.md) hookに対し、こちらは新しく書くコードの未実装を扱う。
