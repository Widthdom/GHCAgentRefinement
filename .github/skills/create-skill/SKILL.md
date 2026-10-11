---
name: create-skill
description: 'skillを新規作成するときに使う。用途に合ったskill名と最小限の指示を定める。'
---

`.github/skills/<skill-name>/SKILL.md` を作成する。フォルダ名と frontmatter の `name` を一致させ、`description` に用途と使う場面を簡潔に書く。本文には依頼を満たすために必要な指示だけを記載する。

skillの発動条件を継続監視する必要がある場合のみ、`.github/copilot-instructions.md` にskill名と実行条件を記載する。依頼内容から利用場面が明確なskillは列挙せず、`description` による選択に任せる。判定基準や手順はskill側に記載する。
