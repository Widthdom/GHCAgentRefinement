---
name: update-hook
description: '既存hookやその関連ファイルを更新・修正・改善するときに使う。'
---

1. [共通の実装・検証規約](../create-hook/references/hook-conventions.md) を読み、適用する。
2. 対象hookと利用元を確認し、JSON定義、スクリプト、参照先、関連READMEを更新する。
3. 関連テストと [test-hook-configurations.py](../../hooks/tests/test-hook-configurations.py) の期待値を変更内容に合わせて更新し、実行する。
