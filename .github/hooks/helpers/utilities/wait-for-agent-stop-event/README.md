# wait-for-agent-stop-event utility README

## 役割
agentStop hookが最終応答やツール実行の結果を読む前に、それらがトランスクリプトに書き込まれるのを待つ。トランスクリプトを読むagentStop hookが共有する。

## 待ち方
Copilotはトランスクリプトへ非同期に追記するため、agentStop hookの発動直後は、最終応答や最後のツール実行の結果がまだ書き込まれていないことがある。イベントは発生順に追記されるため、本hook自身の `hook.start`（hook入力と同じ `timestamp` を持つagentStopのもの）が書き込まれれば、それより前の内容も書き込み済みとみなす。

`hook.start` が現れるまで最大5秒待つ。期限を過ぎた場合は待つのをやめ、その時点の内容で呼び出し元の処理を続ける。
