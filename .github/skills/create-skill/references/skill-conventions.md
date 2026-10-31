# skillの共通規約

1. 正本は `.github/skills/<skill-name>/SKILL.md` に置き、フォルダー名とfrontmatterの `name` を一致させる。`description` には用途と発動条件を「〜ときに使う。」の形で簡潔に書く。本文には実行手順、判断基準、手順に必要な参照のみを書き、skill自体の発動条件は書かない。
2. Codex用の `.agents/skills/<skill-name>/SKILL.md` とClaude用の `.claude/skills/<skill-name>/SKILL.md` を、ラッパーとして通常のファイルで置く。`name` と `description` は正本と同じにし、本文は次の1文のみにする。正本の追加・削除、`name`・`description` の変更時はラッパーも合わせる。
   ```
   [正本のskill](../../../.github/skills/<skill-name>/SKILL.md) を読み、その手順に従う。正本内の相対パスは、正本の `SKILL.md` があるディレクトリを基準に解決する。
   ```
3. 手順にスクリプトが必要な場合は、同じフォルダーの `scripts/` に置き、説明を `scripts/README.md` に書く。
4. 設計理由や背景説明は、必要な場合のみ同じフォルダーの `README.md` に書く。
5. 依頼された作業の内容からは発動を予期できず、作業中に発動条件を継続監視する必要があるskillのみ、 [.github/copilot-instructions.md](../../../copilot-instructions.md) にskill名と、その `description` の発動条件を参照する監視指示を書く。
6. 本文には `description`、 [.github/copilot-instructions.md](../../../copilot-instructions.md) 、共通規約と同じことを書かない。