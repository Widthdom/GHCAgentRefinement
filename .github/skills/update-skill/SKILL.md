---
name: update-skill
description: '既存skillやその関連ファイルを更新・修正・改善・削除するときに使う。'
---

1. [共通の記述規約](../create-skill/references/skill-conventions.md) を読み、適用する。
2. 正本と関連ファイルを更新し、ラッパーを合わせる。改名・削除では `git grep` で参照元を探して更新する。
3. スクリプトを変更した場合は、そのスクリプトを使うテストを `git grep` で探し、存在すれば実行する。
