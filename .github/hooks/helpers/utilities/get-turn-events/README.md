# get-turn-events utility README

## 役割
トランスクリプトをユーザーの依頼ごとに分け、最新の依頼の範囲のイベントを取り出す。依頼の範囲を検査するagentStop hookが共有する。

## 依頼の区切り方
- `delivery` が `queued` でない `user.message` を、新しい依頼の始まりとする。
- hookの差し戻しやユーザーの割り込みは `delivery: "queued"` の `user.message` として記録されるため、依頼の区切りとせず、同じ依頼のイベントとして扱う。
- 最新の依頼の中に、hookの差し戻しの文で始まる `queued` の `user.message` があれば、そのhookは差し戻し済みとみなす。同じ依頼での差し戻しを1回に限るために使う。
- 依頼の文として、依頼の `user.message` と、ユーザーの割り込みの `queued` の `user.message` を集める。hookの差し戻しは、agentStopの `hook.end` の `output`（`decision` が `block`）の `reason` で始まる `queued` の `user.message` として記録されるため、依頼の文から除く。
- 最終応答は、依頼の範囲で最後の、空白以外を含む `assistant.message` とする。

## ツール呼び出しの取り出し方
- `tool.execution_start` と `tool.execution_complete` を `toolCallId` で対応させ、完了した順に返す。完了していない呼び出しは最後に加える。
- シェル実行の終了コードは、完了イベントの `shellExecution.exitCode` から取得する。拒否などで実行されなかった場合は記録されないため、終了コードなしとする。
