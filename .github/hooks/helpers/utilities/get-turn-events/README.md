# get-turn-events utility README

## 役割
トランスクリプトをユーザーの依頼ごとに分け、最新の依頼の範囲のイベントを取り出す。依頼の範囲を検査するagentStop hookが共有する。

## 依頼の区切り方
- `delivery` が `queued` でない `user.message` を、新しい依頼の始まりとする。
- hookの差し戻しやユーザーの割り込みは `delivery: "queued"` の `user.message` として記録されるため、依頼の区切りとせず、同じ依頼のイベントとして扱う。
- 最新の依頼の中に、hookの差し戻しの文で始まる `queued` の `user.message` があれば、そのhookは差し戻し済みとみなす。同じ依頼での差し戻しを1回に限るために使う。
- 最終応答は、依頼の範囲で最後の、空白以外を含む `assistant.message` とする。
