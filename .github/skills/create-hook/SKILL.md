---
name: create-hook
description: 'hookを新規作成するときに使う。イベント選定、設定、スクリプト実装、動作検証を行う。'
---

対象ハーネスの公式仕様に合うイベント名と入出力形式を使う。

hook定義は `.github/hooks/<hook-name>.json`、実行スクリプトは `.github/hooks/<hook-name>.ps1`、補助スクリプトは `.github/hooks/helpers/<hook-name>/` に配置する。

実装後、JSON構文と正常系・制御系の動作を検証する。
