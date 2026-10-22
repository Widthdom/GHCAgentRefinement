---
name: create-hook
description: 'hookを新規作成・追加するときに使う。'
---

1. [共通の実装・検証規約](./references/hook-conventions.md) を読み、適用する。
2. 対象イベントと既存hookを確認し、同じイベントのJSON定義に新しいhookを追加する。既存hookの設定や実行順序を意図せず変えない。
3. 実行スクリプト、必要な補助ファイルと説明、個別hookのテストを作成する。
4. 設定全体を横断して検証する [test-hook-configurations.py](../../hooks/tests/test-hook-configurations.py) の期待値を追加した設定に合わせて更新し、個別hookのテストとともに実行する。
