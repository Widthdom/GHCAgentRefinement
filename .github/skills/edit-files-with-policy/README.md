# edit-files-with-policy skill README

## 役割

[block-shell-file-edits hook](../../hooks/helpers/events/preToolUse/block-shell-file-edits/README.md) の拒否を受けて作業を止めてしまうエージェントがいるため、ファイル編集ツールでのやり直し方を示す。拒否される前に、シェルで書き換えようとした段階でも発動するため、本文は拒否の有無によらない書き方にしている。

## ファイル編集ツール名を書かない理由

拒否理由にCopilotのツール名が示される。拒否される前に発動した場合も、エージェントは自身のファイル編集ツールを把握しているため、「ファイル編集ツール」で通じる。

また、ツール名はエージェントごとに異なる（Copilotの `edit`・`create`、Claudeの `Edit`・`Write` など）。このskillはラッパーを通してCopilot以外も読むため、特定のエージェントのファイル編集ツール名は書かない。

## ファイル編集ツールで読み直す理由

シェルで書き換えようとしたエージェントは、事前に `cat` などで内容を読んでいることが多い。[require-read-before-edit hook](../../hooks/helpers/events/preToolUse/require-read-before-edit/README.md) はシェルでの閲覧を記録しないため、そのまま編集すると最初の編集が拒否される。シェルでの書き換えの拒否に続いて編集も拒否されると、作業を止める原因になりうるため、手順1でファイル編集ツール側の閲覧機能で読ませる。

## `mv` を移動・改名に限って許す理由

git管理下のファイルの移動・改名は、エージェントによってはファイル編集ツールで行えない（Copilotの `apply_patch` には `Move to` があるが、Claudeの `Edit`・`Write` にはない）。`mv` を一律に禁止すると、正当な移動・改名まで避けるおそれがあるため、言い換えの例には挙げず、既存ファイルの上書きだけを禁止する。
