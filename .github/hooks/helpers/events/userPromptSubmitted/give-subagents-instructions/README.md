# give-subagents-instructions hook README

## 発動条件
userPromptSubmitted（ユーザがプロンプトを投入する度）

## 処理概要
サブエージェントへの依頼に、共通指示（`.github/copilot-instructions.md`）を添える。

## 処理詳細
1. 入力に `agentId` がなければ、親のセッションへの依頼とみなして何もしない。
2. `agentId` があれば、リポジトリルートの [.github/copilot-instructions.md](../../../../../copilot-instructions.md) を読み込み、`additionalContext` として依頼文に添える。

## 前提条件
- 指示ファイルが存在しない場合、または内容が空の場合は、サブエージェントへの依頼のときだけエラーとして終了する。

## 制約と限界
- 古いバージョンのCopilotでは、サブエージェントへの依頼の入力に `agentId` が含まれず、親への依頼と区別できないため、指示を渡さない。

## 備考
- サブエージェントではsessionStartが発火しないため、親のセッションに指示を渡す [load-copilot-instructions](../../sessionStart/load-copilot-instructions/README.md) hookの指示が届かない。サブエージェントの報告が口調や `(planned)` の目印などの共通指示に従うよう、同じ指示ファイルを渡す。
- サブエージェントへの依頼でもuserPromptSubmittedは発火し、入力に `agentId`・`agentType`・`parentAgentId` が含まれることをトランスクリプトで確認している。userPromptSubmittedの `additionalContext` は、依頼文に `<system_reminder>` として追加される。
