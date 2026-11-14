# read-transcript-events utility README

## 役割
セッションのトランスクリプト（`events.jsonl`）を読む。最終応答やツール実行の結果が書き込まれるのを待ち、イベントをユーザーの依頼ごとに分けて、依頼の範囲を検査するagentStop hookが必要な情報を取り出す。

|関数|役割|
|--|--|
|`read_transcript_events`|イベントを1件ずつ検証して読む|
|`wait_for_agent_stop_event`|agentStop hookの発動時点で、それより前のイベントが書き込まれるのを待つ|
|`get_turn_events`|イベントをユーザーの依頼ごとに分ける|
|`get_latest_turn`・`is_already_requested`・`count_requests_by_hook`・`get_request`・`get_request_texts`・`get_last_assistant_message`・`get_tool_calls`・`get_shell_exit_code`|分けた依頼のイベントから、差し戻しの有無・依頼の文・最終応答・ツール呼び出しなどを取り出す|
|`get_event_time_ms`|イベントの時刻（`timestamp`、ISO 8601）をUnixミリ秒にする|

## 読み方
- 空行を除く各行を、JSONオブジェクトのイベントとして読む。BOMは除く。JSONとして解析できない行、オブジェクトでない行、`data` がオブジェクトでない行は、行番号を示して異常とする。`data` がない行は、空のオブジェクトとして扱う。
- 待機中に限り、JSONとして解析できない行は、非同期の追記中の不完全な行として読み飛ばす。

## 待ち方
Copilotはトランスクリプトへ非同期に追記するため、agentStop hookの発動直後は、最終応答や最後のツール実行の結果がまだ書き込まれていないことがある。イベントは発生順に追記されるため、本hook自身のagentStopの `hook.start` が書き込まれれば、それより前の内容も書き込み済みとみなす。

本hook自身の `hook.start` は、イベント自体の時刻がhook入力の `timestamp`（Unixミリ秒）の1秒前以降であるagentStopの `hook.start` とする。過去のターンのagentStopと取り違えないためで、1秒の幅は、2つの時刻を記録する処理の差を見込んだもの。

イベントの時刻を読めない `hook.start` に限り、`input` の `timestamp` がhook入力と一致するものを本hook自身のものとみなす。`hook.start` の `input` はhookの標準入力と形式が異なる場合がある（[共通規約](../../../../skills/create-hook/references/hook-conventions.md)の項目1）ため、イベントの時刻を主に使い、`input` での照合は補助にとどめる。形式が変わっても、agentStop hookが毎回期限まで待つことを防ぐ。

`hook.start` が現れるまで最大5秒待つ。期限を過ぎた場合は待つのをやめ、その時点の内容で呼び出し元の処理を続ける。hook入力の `timestamp` が数値でなければ、照合できないため待たない。

## 依頼の区切り方
- `delivery` が `queued` でない `user.message` を、新しい依頼の始まりとする。
- hookの差し戻しやユーザーの割り込みは `delivery: "queued"` の `user.message` として記録されるため、依頼の区切りとせず、同じ依頼のイベントとして扱う。
- 依頼の中に、hookの差し戻しの文で始まる `queued` の `user.message` があれば、そのhookは差し戻し済みとみなす。同じ依頼での差し戻しを1回に限るために使う。回数に上限を設けるhookは、その数を数える。
- 依頼の文として、依頼の `user.message` と、ユーザーの割り込みの `queued` の `user.message` を集める。hookの差し戻しは、agentStopの `hook.end` の `output`（`decision` が `block`）の `reason` で始まる `queued` の `user.message` として記録されるため、依頼の文から除く。
- 最終応答は、依頼の範囲で最後の、空白以外を含む `assistant.message` とする。

## ツール呼び出しの取り出し方
- `tool.execution_start` と `tool.execution_complete` を `toolCallId` で対応させ、完了した順に返す。完了していない呼び出しは最後に加える。
- シェル実行の終了コードは、完了イベントの `shellExecution.exitCode` から取得する。拒否などで実行されなかった場合は記録されないため、終了コードなしとする。

## 背景
- macOSで、デバッグモードが有効でもdebug-add-response-footerがフッターの修正を求めず、`No assistant response was found in the transcript.` で異常終了していた。agentStopの発動時点で最終応答の `assistant.message` が書き込まれておらず、同じターンのそれまでの応答がツール呼び出しだけ（本文が空）だったため、本文を持つ応答が見つからなかった。この対策として待つようになった。
- 待機は書き込みを待つための追加の手順で、期限を過ぎても異常終了にはせず、待たない場合と同じ処理を続ける。書き込まれていない内容の扱いは、呼び出し元のhookがそれぞれの検証で決める。
