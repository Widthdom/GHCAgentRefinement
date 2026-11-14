# detect-temporary-code hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
今回の依頼で書き込み、最終的なファイルに残っている作業途中のコード（デバッグ用の出力やデバッガーの呼び出し、応答で触れていない仮実装）を検出し、削除・実装するか、残す意図を応答で示すよう差し戻す。

## 処理詳細
1. [temporary-code-definitions.json](./temporary-code-definitions.json) から、対象外のファイルのパス（`excludedFilePatterns`）、デバッグ用のコードの正規表現（`debugPatterns`）、仮実装の正規表現（`placeholderPatterns`）、応答で未実装に触れているとみなす正規表現（`placeholderDisclosurePatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（[wait-for-agent-stop-event](../../../utilities/wait-for-agent-stop-event/README.md)）。
3. ユーザーの最新の依頼以降について、最終応答と、編集系ツール（`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`）の書き込む内容と置換前の内容を集める。
   - 各ツールの引数は [edit-tool-changes](../../../utilities/edit-tool-changes/README.md) の規則で解析する。
4. 次の種類の順に、cwdからの相対パス（`/` 区切り）が `excludedFilePatterns` に一致しないファイルについて、書き込む内容のうち種類の正規表現に一致する行を探す。前後の空白を除いて同じ行が置換前の内容にあるもの（元からあるもの）と、ファイルの現在の内容にないもの（後の編集で消えたもの）は除く。同じファイルの同じ行は、同じ種類の複数の項目に一致しても1件にまとめる。この照合は [find-introduced-lines-by-regex.py](../../../utilities/find-introduced-lines-by-regex/find-introduced-lines-by-regex.py) で行う。
   1. デバッグ用のコード（`debugPatterns`）
   2. 仮実装（`placeholderPatterns`）。最終応答が `placeholderDisclosurePatterns` に一致する（「未実装」「スタブ」「TODO」「not implemented」などに触れている）場合は探さない。
5. 該当する行が最初に見つかった種類について、cwd配下のファイルはcwdからの相対パス（`/` 区切り）で最大10件を列挙し、英語の理由とともに `{"decision":"block","reason":"..."}` を標準出力して差し戻す。
   - デバッグ用のコード：削除するか、意図した出力なら応答でその旨を示すよう求める。
   - 仮実装：実装するか、残す箇所と理由を応答に明記するよう求める。

## 検出する内容

|種類|例|
|--|--|
|consoleへの出力|`console.log(...)`・`console.debug(...)`・`console.trace(...)`・`console.dir(...)`・`console.table(...)`|
|debugger文|`debugger;`|
|Pythonのデバッガー|`breakpoint()`・`pdb.set_trace()`・`import pdb`・`import ipdb`|
|Rubyのデバッガー|`binding.pry`・`binding.irb`・`byebug`|
|Rustのdbgマクロ|`dbg!(...)`|
|PHPのダンプ|`var_dump(...)`|
|.NETのデバッガー|`Debugger.Break()`・`Debugger.Launch()`|
|デバッグ用の印を付けた出力|`print(f"DEBUG: ...")`・`print(">>> here")`・`fmt.Println("XXX", v)`・`System.out.println("HERE")`・`Console.WriteLine("[DEBUG] ...")`・`Write-Host "*** ..."`|
|NotImplementedException|`throw new NotImplementedException();`|
|NotImplementedError|`raise NotImplementedError`|
|未実装を示す例外|`throw new Error('Not implemented')`・`throw new UnsupportedOperationException("TODO")`|
|未実装を示すpanic|`panic("not implemented")`|
|未実装のマクロ・関数|`todo!()`・`unimplemented!()`・`TODO()`（Kotlin）|
|TODOコメント|`// TODO`・`# TODO`・`/* TBD */`・`-- TODO`|
|実装の先送りを示すコメント|`// 後で実装`・`# 未実装`・`/* implement later */`・`// stub implementation`|

## 例外
- 文書と設定のファイル（`*.md`・`*.markdown`・`*.rst`・`*.txt`・`*.adoc`・`*.json`）は対象外とする。
- 書き込む時点では拒否せず、応答終了時に最終的なファイルに残っているものだけを検出する。調査の途中でデバッグ用のコードを入れることや、編集の途中で仮実装を置くことは正当な手順のため、後で消したものは差し戻さない。
- 誤検知を避けるため、`print(...)` などの出力は、文字列が `DEBUG`・`debug:`・`XXX`・`HERE`・`>>>`・`***` で始まるものだけを対象とする。コマンドラインツールの出力やエラーメッセージなどの通常の出力と、`logger.debug(...)` などのロガーは対象外。
- `FIXME` は対象外とする。[add-fixme-comment](../../../../../skills/add-fixme-comment/SKILL.md) skillが依頼範囲外の不具合の目印として意図的に書き込むため。
- 応答で未実装に触れていても、デバッグ用のコードは検査する。
- サブエージェントの停止は対象外。
- 依頼の区切りは [get-turn-events](../../../utilities/get-turn-events/README.md) の規則に従う。
- 最新の依頼の編集だけを対象とする。それより前の依頼の編集やユーザーの変更で入ったものは検出しない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../../preToolUse/block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- コマンドラインツールの出力など意図した `console.log` でも、一度は差し戻す。応答でその旨を示せば、同じ依頼では再び差し戻さない。
- 抽象メソッドの `raise NotImplementedError` など意図した仮実装でも、応答で触れていなければ差し戻す。応答で残す箇所と理由を示せば停止できる。
- 同じ依頼で差し戻すのは、種類（デバッグ用のコード・仮実装）ごとに1回だけ。差し戻した種類は2回目以降の応答で検査しない。差し戻しの理由の書き出しで種類を区別する。
- 両方の種類が当てはまる場合も、1回の差し戻しでは1つだけを示す。残りは次の応答の終了時に検査する。
- 正規表現で検出するため、定義にない形式（印のない `print(x)`、空の本体、固定値を返すだけの本体、Pythonの `pass` など）は見逃す。文字列リテラル内やコメント内の一致も検出する。

## 備考
- 原因を調べるために入れたデバッグ用の出力やデバッガーの呼び出しを消し忘れたり、仮実装を残したまま、それに触れずに作業を完了として報告したりする事故を防ぐためのhook。デバッグ用の出力は出力を汚し、データを漏らすことがあり、デバッガーの呼び出しはプログラムを止める。
- 既存コードの省略による消失を防ぐ [block-elided-rewrites](../../preToolUse/block-elided-rewrites/README.md) hookに対し、こちらは新しく書くコードの作業途中の残りを扱う。
- 正規表現は各行の行頭・行末に `^`・`$` が一致するよう、複数行モードで照合する。
