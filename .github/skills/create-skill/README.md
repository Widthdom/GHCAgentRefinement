# create-skill skill README

## 作成と更新の分担

[create-skill](./SKILL.md) は新規作成、[update-skill](../update-skill/SKILL.md) は既存skillの更新を担当する。

## 共通規約

両方に必要な記述規約は [共通規約](./references/skill-conventions.md) を参照する。共通規約は実行に必要なため、背景説明とは異なり各skillから明示的に読み込む。

## CodexとClaudeとの共有

CopilotとCodex・Claudeで手順を二重管理しないため、CodexとClaudeには正本を参照するラッパーを用意する。配置や同期のルールは共通規約に集約する。

## 実行指示と背景説明の分離

Skillの `SKILL.md` は、skillが使われるときにコンテキストへ読み込まれる実行指示。背景説明や設計理由まで含めると、作業に直接必要でない情報がコンテキストウィンドウを圧迫し、実行指示に使える余地を減らす。

そのため、`SKILL.md` には実行に必要な手順と判断基準を記載し、「なぜそうするか」などの背景説明は同じフォルダの `README.md` に分ける。READMEは必要時に明示的に参照する資料とし、実行時に常に読み込まれる前提にしない。READMEを読む必要がないskillでは、背景を埋めるためだけにREADMEを作らない。
