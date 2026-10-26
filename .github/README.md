# .github README

## 前提条件

このリポジトリのhookはCopilot SDK形式のみを対象とする。`.py` の実行にはPython 3.14以降が必要で、起動コマンドはmacOS/Linuxでは `python3`、Windowsでは `python` を使う。VS CodeのCopilot Chatで使う場合は、Session Targetを `Copilot` にする。`Copilot` targetはCLIと同じSDK hook実装を使うが、利用中のVS Codeが対象イベントに対応するか確認する。`Local`、`Claude`、`Codex`などSDK以外の実行環境は対象外とし、互換処理やフォールバックを実装せず、動作も検証しない。

## 主なhook

### セッション開始時発動

|hook|概要|参考|
|--|--|--|
|Session Targetの確認|Session Targetが `Copilot` の状態で使用できるよう支援する。|[check-target-session hook README](./hooks/helpers/events/sessionStart/check-target-session/README.md)|
|Pythonの確認|Python 3.14以降（macOS/Linuxでは `python3`、Windowsでは `python`）のインストールを支援する。|[check-python hook README](./hooks/helpers/events/sessionStart/check-python/README.md)|
|Gitの確認|Git 2.34.1以降のインストールを支援する。|[check-git hook README](./hooks/helpers/events/sessionStart/check-git/README.md)|
|ripgrepの確認|ripgrep 13.0.0以降のインストールを支援する。|[check-ripgrep hook README](./hooks/helpers/events/sessionStart/check-ripgrep/README.md)|
|Copilot Instructionsの読み込み|`copilot-instructions.md` を明に追加コンテキストとして渡す。|[load-copilot-instructions hook README](./hooks/helpers/events/sessionStart/load-copilot-instructions/README.md)|

### bashまたはpowershellツールの実行前に発動

|hook|概要|参考|
|--|--|--|
|危険コマンドの実行拒否|[`blocked-command-definitions.json`](./hooks/helpers/events/preToolUse/block-commands/blocked-command-definitions.json)にて定義されたコマンドの実行を拒否する。|[block-commands hook README](./hooks/helpers/events/preToolUse/block-commands/README.md)|

### ツール実行３回失敗で方針転換

同じツール・引数・エラーで5分以内に3回失敗した場合、原因の整理と方針転換を促す。詳細は[detect-repeated-failures hook README](./hooks/helpers/events/postToolUseFailure/detect-repeated-failures/README.md)を参照。

## デバッグモード

プロンプトの先頭に `??` を付与するとデバッグモードになり、 `debug-` 接頭辞付きのhookが発動するようになる。詳細は[set-debug-mode hook README](./hooks/helpers/events/userPromptSubmitted/set-debug-mode/README.md)を参照。

## 敵対的レビュー

末尾 `;;` であるプロンプトに対する処理終了時、変更差分に対するレビューを[敵対的レビューエージェント](./agents/adversarial-review.agent.md)に依頼する。詳細は[request-adversarial-review hook README](./hooks/helpers/events/agentStop/request-adversarial-review/README.md)を参照。
