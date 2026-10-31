# debug-add-response-footer hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理詳細
1. セッションIDに対応するデバッグモード状態を読み取る。
2. デバッグモードが有効な場合、トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（Copilotはトランスクリプトへ非同期に追記するため、発動直後は最終応答が未書き込みのことがある）。
3. トランスクリプトから直近の応答で使用したhookとskillを特定する。さらに、[hookランナー](../../../runner/README.md)の記録から今回のターンで実際に機能したhookを特定し、現在時刻（JST）とともにフッタを作成する。
4. 応答末尾に正しいフッターがない場合、本文を維持して正しいフッタを追加または置換するよう応答を差し止める。

## 前提条件
- 「Hooks fired」には、agentStopで動くhookの結果を含めない。agentStop hookは本hookと同時に動くため、同じ応答終了時の結果をフッターの作成時点では確定できない。フッターの見出しにも `excluding agentStop hooks` と明記する。
- 「Hooks fired」は [hookランナー](../../../runner/README.md) を通して起動したhookだけが対象。ランナーを通さない `check-python` と `block-commands` は含まれない。

## フッターの項目
|見出し|内容|
|--|--|
|`Hooks used (inferred from event history)`|トランスクリプトに記録されたイベント種別から推定した、起動した可能性のあるhook|
|`Hooks fired (recorded by hook runner, excluding agentStop hooks)`|hookランナーの記録で、終了コードが0以外か、空でない出力を返したhook|
|`Skills used (inferred from event history)`|トランスクリプトに記録されたskillツールの呼び出し|
|`Current time`|フッター作成時の現在時刻（JST）|

## ターンの区切り
- 「Hooks fired」は、直近のユーザー依頼の `userPromptSubmitted` の `hook.start` 以降に起動したhookを対象とする。`userPromptSubmitted` のhookは `user.message` より前に記録されるため。
- フッター修正の依頼（`The final response footer is missing or incorrect.` で始まるuser.message）はターンの区切りとしない。
