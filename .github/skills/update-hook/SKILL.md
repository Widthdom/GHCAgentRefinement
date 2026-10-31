---
name: update-hook
description: '既存hookやその関連ファイルを更新・修正・改善・削除するときに使う。'
---

1. [共通の実装・検証規約](../create-hook/references/hook-conventions.md) を読み、適用する。
2. hook定義（`.github/hooks/<event-name>.json`）、実行スクリプト、README、テストを更新する。移動・改名・削除では `git grep` で参照元を探して更新する。
3. 変更したhookのテスト、[test-all-hooks.py](../../hooks/tests/test-all-hooks.py)、[test-hook-configurations.py](../../hooks/tests/test-hook-configurations.py) を実行する。
