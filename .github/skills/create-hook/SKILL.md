---
name: create-hook
description: 'hookを新規作成・追加するときに使う。'
---

1. [共通の実装・検証規約](./references/hook-conventions.md) を読み、適用する。
2. 対象イベントのhook定義（`.github/hooks/<event-name>.json`）にhookを追加する。既存hookの設定は変えない。
3. 実行スクリプト、README、テストを作成する。
4. 作成したテスト、[test-all-hooks.py](../../hooks/tests/test-all-hooks.py)、[test-hook-configurations.py](../../hooks/tests/test-hook-configurations.py) を実行する。
