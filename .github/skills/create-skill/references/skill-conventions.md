# skillの共通規約

1. 正本は `.github/skills/<skill-name>/SKILL.md` に配置する。フォルダ名と frontmatter の `name` を一致させ、`description` に用途と発動条件を簡潔に書く。本文は実行手順と判断基準に絞り、skill自体の発動条件は記載しない。
2. Codex用に `.agents/skills/<skill-name>/SKILL.md`、Claude用に `.claude/skills/<skill-name>/SKILL.md` を通常のファイルで用意し、`name` と `description` を正本に揃える。本文は正本を読んで従う指示と、正本内の相対パスを正本のディレクトリ基準で解決する指示に絞る。手順や補助ファイルは複製しない。正本の追加・削除や `name`・`description` の変更時はラッパーにも反映する。
3. 設計理由や背景説明が必要な場合だけ、同じフォルダの `README.md` に記載する。手順上必要でないREADMEへの参照は加えない。
4. 発動条件を継続監視する必要があるskillだけ、`.github/copilot-instructions.md` にskill名と、その `description` の発動条件を参照する監視指示を記載する。依頼内容から選べるskillは登録しない。
5. `description`・本文・共通指示の重複を削るか参照に置き換える。共通規約は各skillの本文から明示的に読み込ませる。
