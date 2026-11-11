# write-json-atomically utility README

## 役割
JSONの値を状態ファイルに保存する。保存先と同じフォルダーの一時ファイル（`.tmp-` で始まる名前）に書いてから `os.replace` で置き換えるため、別のhookが書き込みの途中で読んでも、不完全な内容を読ませない。状態ファイルを書くhookが共有する。

## 書き込みの規則
- 親のフォルダーがなければ作る。
- JSONはBOMなしのUTF-8で、`ensure_ascii=False`・区切りの空白なし（`{"enabled":true}`）で書く。
- 書き込みや置き換えに失敗した場合は一時ファイルを消し、例外をそのまま呼び出し元に送出する。置き換える前の状態ファイルは変わらない。

## 利用するhook
- [set-debug-mode](../../events/userPromptSubmitted/set-debug-mode/README.md)
- [set-adversarial-review-state](../../events/userPromptSubmitted/set-adversarial-review-state/README.md)
- [request-adversarial-review](../../events/agentStop/request-adversarial-review/README.md)
- [record-hook-change-requests](../../events/userPromptSubmitted/record-hook-change-requests/README.md)（[hook-change-request-state.py](../hook-change-request-state/hook-change-request-state.py) を通して使う）
