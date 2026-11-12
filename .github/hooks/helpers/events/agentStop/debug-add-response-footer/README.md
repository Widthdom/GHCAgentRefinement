# debug-add-response-footer hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
デバッグモードのとき、使用したhook・skillと実際に機能したhook、現在時刻を並べたフッターを、最終応答の末尾に付けさせる。

## 処理詳細
1. 入力に `agentId` があれば何もしない（サブエージェントの停止）。
2. セッションIDに対応するデバッグモード状態を読み取る。
3. デバッグモードが有効な場合だけ、入力の `transcriptPath` を検証し、トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（[wait-for-agent-stop-event](../../../utilities/wait-for-agent-stop-event/README.md)）。無効な場合は、`transcriptPath` が不正でも正常終了する。
4. ユーザーの最新の依頼以降に本hookが2回差し戻していれば、何もしない。
5. トランスクリプトから直近の応答で使用したhookとskillを特定する。さらに、[hookランナー](../../../runner/README.md)の記録から今回のターンで実際に機能したhookを特定し、現在時刻（JST）とともにフッタを作成する。
6. 応答末尾に正しいフッターがない場合、本文を維持して正しいフッタを追加または置換するよう応答を差し止める。

## フッターの項目
|見出し|内容|
|--|--|
|`Hooks used (inferred from event history)`|トランスクリプトに記録されたイベント種別から推定した、起動した可能性のあるhook|
|`Hooks fired (recorded by hook runner, excluding agentStop hooks)`|hookランナーの記録で、終了コードが0以外か、空・空白だけ・空のJSONオブジェクト（`{}`）以外の出力を返したhook|
|`Skills used (inferred from event history)`|トランスクリプトに記録されたskillツールの呼び出し|
|`Current time`|フッター作成時の現在時刻（JST）|

## ターンの区切り
- 依頼の区切りは [get-turn-events](../../../utilities/get-turn-events/README.md) の規則に従い、`delivery` が `queued` でない `user.message` とする。フッター修正の依頼や他のhookの差し戻しは `queued` として記録されるため、ターンの区切りとせず、同じ依頼で使ったhook・skillを引き続き集計する。
- 「Hooks fired」は、直近のユーザー依頼の `userPromptSubmitted` の `hook.start` 以降に起動したhookを対象とする。`userPromptSubmitted` のhookは `user.message` より前に記録されるため。
- skillツールの引数は、JSON文字列で記録された場合も解析して読む（[unwrap-tool-args](../../../utilities/unwrap-tool-args/unwrap-tool-args.py)）。

## 前提条件
- デバッグモード（[set-debug-mode](../../userPromptSubmitted/set-debug-mode/README.md) hookで有効化）が有効なときだけ動く。

## 例外
- サブエージェントの停止は対象外とする。フッターを付けるのは、ユーザーへ報告する親の最終応答のため。

## 制約と限界
- 同じ依頼で差し戻すのは2回まで。フッターの時刻や機能したhookは差し戻しのたびに変わるため、合わせ損ねても停止できるようにする。1回では時刻の繰り上がりなどで合わせ損ねた場合に直せないため、他のagentStop hook（1回）より1回多くする。差し戻しの回数は、`The final response footer is missing or incorrect.` で始まるuser.messageで数える。
- 「Hooks fired」には、agentStopで動くhookの結果を含めない。agentStop hookは本hookと同時に動くため、同じ応答終了時の結果をフッターの作成時点では確定できない。フッターの見出しにも `excluding agentStop hooks` と明記する。
- 「Hooks fired」は [hookランナー](../../../runner/README.md) を通して起動したhookだけが対象。ランナーを通さない [check-python](../../sessionStart/check-python/README.md) と [block-commands](../../preToolUse/block-commands/README.md) は含まれない。
