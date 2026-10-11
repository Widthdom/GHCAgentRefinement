---
name: create-hook
description: 'hookを新規作成するときに使う。イベント選定、設定、スクリプト実装、動作検証を行う。'
---

対象ハーネスの公式仕様に合うイベント名と入出力形式を使う。

hook定義は `.github/hooks/<hook-name>.json`、実行スクリプトは `.github/hooks/<hook-name>.ps1`、補助スクリプトは `.github/hooks/helpers/<hook-name>/` に配置する。

デバッグモードでのみ有効になるhookの名前には `debug-` 接頭辞を付ける。

`debug-` hookを作成するときは、`check-debug-mode.ps1`が保存するセッション単位の状態について、保存先とJSON形式を実装から確認する。hook入力のセッションIDから対応する状態を読み、状態ファイルがない場合または `enabled` が `true` でない場合は、本来の処理を行わず正常終了する。状態の読み取り方は `debug-add-response-footer.ps1` を実装例とする。

実装後、JSON構文と正常系・制御系の動作を検証する。
