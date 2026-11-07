# detect-debug-leftovers hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
今回の依頼で書き込み、最終的なファイルに残っているデバッグ用の出力やデバッガーの呼び出しを検出し、削除するか意図を応答で示すよう差し戻す。

## 処理詳細
1. [debug-leftover-definitions.json](./debug-leftover-definitions.json) から、対象外のファイルのパス（`excludedFilePatterns`）と、デバッグ用のコードの正規表現（`debugPatterns`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（Copilotはトランスクリプトへ非同期に追記するため、発動直後は最終応答が未書き込みのことがある）。
3. ユーザーの最新の依頼以降について、最終応答と、編集系ツール（`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`）の書き込む内容と置換前の内容を集める。
   - 各ツールの引数は [extract-edit-tool-changes](../../../utilities/extract-edit-tool-changes/README.md) の規則で解析する。
4. cwdからの相対パス（`/` 区切り）が `excludedFilePatterns` に一致しないファイルについて、書き込む内容のうち `debugPatterns` に一致する行を探す。前後の空白を除いて同じ行が置換前の内容にあるもの（元からあるもの）と、ファイルの現在の内容にないもの（後の編集で消えたもの）は除く。同じファイルの同じ行は、複数の種類に一致しても1件にまとめる。この照合は [find-introduced-lines-by-regex.py](../../../utilities/find-introduced-lines-by-regex/find-introduced-lines-by-regex.py) で行う。
5. 該当する行があれば、cwd配下のファイルはcwdからの相対パス（`/` 区切り）で最大10件を列挙し、削除するか、意図した出力なら応答でその旨を示すよう求める英語の理由とともに `{"decision":"block","reason":"..."}` を標準出力して差し戻す。

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
|対象外のファイル|`*.md`・`*.markdown`・`*.rst`・`*.txt`・`*.adoc`・`*.json`|

## 例外
- 書き込む時点では拒否せず、応答終了時に最終的なファイルに残っているものだけを検出する。調査の途中で入れて、後で消したものは差し戻さない。
- 誤検知を避けるため、`print(...)` などの出力は、文字列が `DEBUG`・`debug:`・`XXX`・`HERE`・`>>>`・`***` で始まるものだけを対象とする。コマンドラインツールの出力やエラーメッセージなどの通常の出力と、`logger.debug(...)` などのロガーは対象外。
- サブエージェントの停止は対象外。
- hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
- 最新の依頼の編集だけを対象とする。それより前の依頼の編集やユーザーの変更で入ったものは検出しない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../../preToolUse/block-shell-file-edits/README.md) hookが拒否する。

## 制約と限界
- コマンドラインツールの出力など意図した `console.log` でも、一度は差し戻す。応答でその旨を示せば、同じ依頼では再び差し戻さない。
- 同じ依頼で差し戻すのは1回だけ。2回目以降の応答は検査しない。
- 正規表現で検出するため、定義にない形式（印のない `print(x)` など）は見逃す。文字列リテラル内やコメント内の一致も検出する。

## 備考
- 原因を調べるために入れたデバッグ用の出力やデバッガーの呼び出しを、消し忘れたまま作業を終える事故を防ぐためのhook。出力を汚し、データを漏らすことがあり、デバッガーの呼び出しはプログラムを止める。
