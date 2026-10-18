# load-copilot-instructions hook README

## 発動条件
sessionStart（セッション開始時）

## 処理詳細
リポジトリルートの `.github/copilot-instructions.md` を読み込み、`additionalContext` としてセッションに追加する。

## 備考
- 指示ファイルが存在しない場合、または内容が空の場合はエラーとして終了する。
- GitHub Copilotは、`.github/copilot-instructions.md` を自動で読み込むのが通常仕様ではあるが、稀に読み込まれない場合があるためそれをカバーする狙いがある。