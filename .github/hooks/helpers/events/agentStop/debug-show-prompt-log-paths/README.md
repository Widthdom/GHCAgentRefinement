# debug-show-prompt-log-paths hook README

## 発動条件

agentStop（エージェントの応答終了時）

## 処理概要

デバッグモードのとき、[debug-log-transformed-prompt](../../userPromptTransformed/debug-log-transformed-prompt/README.md) hookが保存したプロンプト変換ログの保存先を、応答本文とは別に表示する。

## 処理詳細

1. セッションのデバッグモードを確認し、無効なら終了する。
2. トランスクリプトに記録された今回の依頼の `userPromptTransformed` の発動時刻と、同じセッションのログファイル名の先頭にある時刻を照合し、対象のログを取得する。
3. 保存先をprogress形式で標準出力し、応答本文とは別に表示する。

## 前提条件

- デバッグモード（[set-debug-mode](../../userPromptSubmitted/set-debug-mode/README.md) hookで有効化）が有効なときだけ動く。

## 例外

- 対象は最初の応答終了までのログ。内部的な応答継続で作られたログは表示対象に含めない。

## 備考

- 応答継続時も再表示するため、最後の応答の後でも保存先を確認できる。
