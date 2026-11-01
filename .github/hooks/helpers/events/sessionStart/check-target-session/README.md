# check-target-session hook README

## 発動条件
sessionStart（セッション開始時）

## 処理概要
Copilot CLI・SDK以外（VS CodeのCopilot Chat）から起動されたセッションで、Session Targetを `Copilot` に切り替えるよう案内する。

## 処理詳細
VS CodeのCopilot Chatであった場合は、Session Targetを `Copilot` にして新しいセッションで作業するよう案内する。
