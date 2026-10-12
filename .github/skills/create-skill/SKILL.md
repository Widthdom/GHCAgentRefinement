---
name: create-skill
description: 'skillを新規作成するときに使う。'
---

`.github/skills/<skill-name>/SKILL.md` を作成する。フォルダ名と frontmatter の `name` を一致させ、`description` に用途と発動条件を簡潔に書く。本文は実行手順と判断基準に絞り、skill自体の発動条件は記載しない。

設計理由や背景説明が必要な場合は、同じフォルダの `README.md` に記載する。`SKILL.md` は実行に必要な手順に絞り、背景を読むことが手順上必要でない限りREADMEへの参照を加えない。

発動条件を継続監視する必要があるskillだけ、`.github/copilot-instructions.md` にskill名と、その `description` の発動条件を参照する監視指示を記載する。依頼内容から選べるskillは登録しない。

仕上げに `description`・本文・共通指示を見直し、重複する条件や説明を削るか参照に置き換える。
