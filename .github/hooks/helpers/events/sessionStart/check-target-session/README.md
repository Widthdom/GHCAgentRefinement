# check-target-session hook README

## 発動条件
sessionStart（セッション開始時）

## 処理概要
VS CodeのLocal harnessから起動されたセッションで、Session Targetを `Copilot` に切り替えるよう案内する。

## 処理詳細
入力の `hook_event_name` が `SessionStart` に完全一致する場合は、Session Targetを `Copilot` にして新しいセッションで作業するよう `systemMessage` で案内する。項目がない場合や別の値の場合は何も出力しない。
