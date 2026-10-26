---
name: create-skill
description: 'skillを新規作成・追加するときに使う。'
---

1. [共通の記述規約](./references/skill-conventions.md) を読み、適用する。
2. 既存skillの役割を確認し、用途が重複しない `.github/skills/<skill-name>/SKILL.md` を作成する。
3. 対応するCodex用ラッパー `.agents/skills/<skill-name>/SKILL.md` を共通規約に従って作成する。
4. 正本とラッパーの `description`・本文・共通指示を見直し、共通規約に照らして配置、frontmatter、参照先と責務の分担を確認する。
