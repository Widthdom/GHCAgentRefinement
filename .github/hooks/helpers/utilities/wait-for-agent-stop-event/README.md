# wait-for-agent-stop-event utility README

## 役割
agentStop hookが最終応答やツール実行の結果を読む前に、それらがトランスクリプトに書き込まれるのを待つ。トランスクリプトを読むagentStop hookが共有する。

## 待ち方
Copilotはトランスクリプトへ非同期に追記するため、agentStop hookの発動直後は、最終応答や最後のツール実行の結果がまだ書き込まれていないことがある。イベントは発生順に追記されるため、本hook自身の `hook.start`（hook入力と同じ `timestamp` を持つagentStopのもの）が書き込まれれば、それより前の内容も書き込み済みとみなす。

`hook.start` の `input` はhookの標準入力と形式が異なる場合がある（[共通規約](../../../../skills/create-hook/references/hook-conventions.md)の項目1）。`timestamp` が一致しなくても、イベント自体の時刻（`timestamp`、ISO 8601）がhook入力の `timestamp`（Unixミリ秒）の1秒前以降であるagentStopの `hook.start` があれば、書き込み済みとみなす。形式が変わっても、agentStop hookが毎回期限まで待つことを防ぐ。1秒の幅は、2つの時刻を記録する処理の差を見込んだもの。

`hook.start` が現れるまで最大5秒待つ。期限を過ぎた場合は待つのをやめ、その時点の内容で呼び出し元の処理を続ける。

## 背景
- macOSで、デバッグモードが有効でもdebug-add-response-footerがフッターの修正を求めず、`No assistant response was found in the transcript.` で異常終了していた。agentStopの発動時点で最終応答の `assistant.message` が書き込まれておらず、同じターンのそれまでの応答がツール呼び出しだけ（本文が空）だったため、本文を持つ応答が見つからなかった。この対策として待つようになった。
- 待機は書き込みを待つための追加の手順で、期限を過ぎても異常終了にはせず、待たない場合と同じ処理を続ける。書き込まれていない内容の扱いは、呼び出し元のhookがそれぞれの検証で決める。
