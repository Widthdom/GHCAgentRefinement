---
name: create-hook
description: 'hookを新規作成・追加するときに使う。'
---

1. [共通の実装・検証規約](./references/hook-conventions.md) を読み、適用する。
2. 対象イベントと既存hookを確認し、同じイベントのJSON定義に新しいhookを追加する。既存hookの設定や実行順序を意図せず変えない。
3. 実行スクリプト、必要な補助ファイルと説明、個別hookのテストを作成する。Pythonのテストは `unittest.TestCase` で記述し、[hook_test_output.py](../../hooks/tests/hook_test_output.py) の `run_tests()` で実行する。進捗、実行件数、実行時間、結果は共通規約に従って標準エラーに出力する。
4. [test-all-hooks.py](../../hooks/tests/test-all-hooks.py) と [test-hook-configurations.py](../../hooks/tests/test-hook-configurations.py) を実行する。
