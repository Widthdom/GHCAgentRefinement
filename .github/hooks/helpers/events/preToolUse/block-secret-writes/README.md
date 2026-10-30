# block-secret-writes hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` の実行前）

## 処理詳細
1. [secret-definitions.json](./secret-definitions.json) から、秘密情報の種類（`name`）と正規表現（`pattern`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 並列に呼び出すツール（`toolCalls`）のうち編集系ツールから、書き込む内容と対象のファイルを特定する。
   - `edit`・`create`・`insert`・`str_replace_editor`：`path`・`command`・置換前の内容（`old_str`）を除く引数の文字列を書き込む内容とする（`str_replace_editor` の `view` は除く）。
   - `apply_patch`：パッチの追加行（`+`）を書き込む内容とし、`*** Add File:`・`*** Update File:`・`*** Move to:` の行から対象を特定する。
3. 書き込む内容が定義の正規表現に一致し、その値が既存の内容（置換前の内容、パッチの削除行、対象ファイルの現在の内容）に含まれていなければ、秘密情報の書き込みとみなす。
4. 対象が環境変数ファイル（`.env`、`.env.local` など）の場合も、内容によらず書き込みとみなす。
5. いずれかに該当すれば、環境変数・シークレットストア・プレースホルダーを使うか、ユーザー自身に編集してもらうよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 検出する秘密情報
|種類|例（伏せ字）|
|--|--|
|Anthropic API key|`sk-ant-` に32文字以上が続くもの|
|OpenAI API key|`sk-`・`sk-proj-` などに32文字以上が続くもの（`sk-ant-` を除く）|
|GitHub token|`ghp_`・`gho_` などに36文字以上、`github_pat_` に60文字以上が続くもの|
|AWS access key ID|`AKIA`・`ASIA` に英大文字と数字16文字が続くもの|
|Google API key|`AIza` に35文字が続くもの|
|Slack token|`xoxb-`・`xoxp-` などに10文字以上が続くもの|
|Stripe live key|`sk_live_`・`rk_live_` に24文字以上が続くもの|
|Private key|`-----BEGIN ... PRIVATE KEY-----`|

## 備考
- 特別なプロンプト指定は不要。
- ユーザーが貼り付けたトークンや、調査中に見つけた鍵をエージェントが設定ファイルやテストに書き込み、そのままコミットや外部送信される事故を、書き込む前に防ぐためのhook。
- 拒否理由には秘密情報の値を含めず、種類とファイルだけを示す。
- 次の場合は新たな漏えいではない、または値ではないため拒否しない。
  - 既存の内容に同じ値がある場合（既に含まれている値の移動や、周辺の編集）
  - 末尾8文字が同じ文字の連続であるプレースホルダー（`ghp_xxxx…`・`AKIA0000…` など）
  - 値のひな形とみなす `.env.example`・`.env.sample`・`.env.template`・`.env.dist`
- 拒否は繰り返す。誤検知で書き込めない場合は、エージェントからユーザーに伝えて、ユーザー自身が編集する。
- テストなどで秘密情報に見える値が必要な場合は、[test-block-secret-writes.py](./test-block-secret-writes.py) のように接頭辞と本体を連結して作り、ファイルに一致する文字列を書かない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../block-shell-file-edits/README.md) hookが拒否する。
- 正規表現で検出するため、定義にない形式の秘密情報（パスワード、独自形式のトークンなど）は見逃す。
- hookの異常終了（不正な入力や定義ファイル）は、Copilotが編集の拒否として扱う。
