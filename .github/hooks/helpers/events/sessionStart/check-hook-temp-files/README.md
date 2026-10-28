# check-hook-temp-files hook README

## 発動条件
sessionStart（セッション開始時）

## 処理詳細
1. 一時フォルダー配下の `ghc-agent-refinement/hook-states/` と `ghc-agent-refinement/debug-logs/` にあるファイルのうち、48時間を超えて更新されていないものを数える。
2. 500件以上あれば、件数と、最初の応答の冒頭で削除用のskill（`/clean-hook-temp-files`）をユーザーに案内するよう求める英語の指示を、`additionalContext` としてエージェントに渡す。

## 備考
- hookの状態とデバッグログは、セッションごとに作られ、hook自身では削除しない。放置したチャットの分が一時フォルダーに溜まっていくため、セッション開始時に知らせる。
- 削除はしない。削除は [clean-hook-temp-files skill](../../../../../skills/clean-hook-temp-files/SKILL.md) で行う。48時間を超えて更新されていないファイルは使用中のセッションのものではないとみなし、削除してよい基準としている。
- 対象の分類と保持期間は [hook-temp-files.py](./hook-temp-files.py) で定め、clean-hook-temp-files skillの削除スクリプトもこれを読み込む。数える基準と削除する基準をずらさない。
- Copilot SDKはhookの出力のうち `systemMessage` を解釈せずに捨てるため、ユーザーへの案内はエージェントを介して行う。
- sessionEndは依頼への応答が終わるたびに発火し、チャットを閉じたときに発火するイベントはないため、チャット1つにつき1回発火するsessionStartで確認する。
- `ghc-agent-refinement/` の直下や、`hook-states/`・`debug-logs/` 以外に残る古い形式のファイルは数えない。
- 数えている間に削除されたファイルや、読めないディレクトリは数えない。
