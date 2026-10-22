# skillの共通規約

1. `.github/skills/<skill-name>/SKILL.md` に配置する。フォルダ名と frontmatter の `name` を一致させ、`description` に用途と発動条件を簡潔に書く。本文は実行手順と判断基準に絞り、skill自体の発動条件は記載しない。
2. 設計理由や背景説明が必要な場合だけ、同じフォルダの `README.md` に記載する。手順上必要でないREADMEへの参照は加えない。
3. 発動条件を継続監視する必要があるskillだけ、`.github/copilot-instructions.md` にskill名と、その `description` の発動条件を参照する監視指示を記載する。依頼内容から選べるskillは登録しない。
4. `description`・本文・共通指示の重複を削るか参照に置き換える。共通規約は各skillの本文から明示的に読み込ませる。
