# load-copilot-instructions hook README

## 発動条件
sessionStart（セッション開始時）

## 処理概要
共通指示（`.github/copilot-instructions.md`）を読み込み、セッションに確実に渡す。

## 処理詳細
リポジトリルートの [.github/copilot-instructions.md](../../../../../copilot-instructions.md) を読み込み、`additionalContext` としてセッションに追加する。

## 前提条件
- 指示ファイルが存在しない場合、または内容が空の場合はエラーとして終了する。

## 制約と限界
- サブエージェントではsessionStartが発火しないため、本hookの指示は届かない。サブエージェントには [give-subagents-instructions](../../userPromptSubmitted/give-subagents-instructions/README.md) hookが、依頼の送信時に同じ指示ファイルを渡す。

## 備考
- GitHub Copilotは、[.github/copilot-instructions.md](../../../../../copilot-instructions.md) を自動で読み込むのが通常仕様ではあるが、稀に読み込まれない場合があるためそれをカバーする狙いがある。
