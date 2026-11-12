# check-target-session hook README

## 発動条件
sessionStart（セッション開始時）

## 処理概要
VS CodeのLocal harnessから起動されたセッションで、Session Targetを `Copilot` に切り替えるよう案内する。

## 処理詳細
入力の `hook_event_name` の有無と表記で起動元のharnessを判別し、VS CodeのLocal harnessの場合だけ、Session Targetを `Copilot` にして新しいセッションで作業するよう `systemMessage` で案内する。

|`hook_event_name`|起動元|動作|
|---|---|---|
|`SessionStart`（先頭大文字）|VS CodeのLocal harness|案内を出力する|
|なし|Copilot CLI・SDK|何も出力しない|
|上記以外（`sessionStart` など）|想定外|何も出力しない|

VS CodeのLocal harnessは、hook定義のイベント名 `sessionStart` とは異なり、先頭大文字の `SessionStart` を渡す。大文字小文字を区別して比較するため、`sessionStart` は一致しない。
