# create-skill skill README

## 作成と更新の分担

[create-skill](./SKILL.md) は新規作成、[update-skill](../update-skill/SKILL.md) は既存skillの更新・削除を担当する。両方が従う記述規約は [共通規約](./references/skill-conventions.md) にまとめ、二重管理しない。

## CodexとClaudeとの共有

手順を二重管理しないため、CodexとClaudeには正本を参照するラッパーだけを置く。

## 実行指示と背景説明の分離

`SKILL.md` はskillの発動時にコンテキストへ読み込まれる。背景説明や設計理由まで含めると、作業に直接必要でない情報がコンテキストウィンドウを圧迫する。

同じフォルダーの `SKILL.md` 以外のファイルは自動では読み込まれず、`SKILL.md` から参照されたときだけエージェントが読みに行く。そのため、手順に必要な `references/` の規約や `scripts/` のスクリプトは `SKILL.md` から参照してよいが、背景説明や設計理由を記載した `README.md` は参照すべきではない。
