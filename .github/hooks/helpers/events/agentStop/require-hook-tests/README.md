# require-hook-tests hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理詳細
1. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（Copilotはトランスクリプトへ非同期に追記するため、発動直後は最後のツール実行の結果が未書き込みのことがある）。
2. ユーザーの最新の依頼以降について、ファイル編集ツール（`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`）で変更に成功したファイルと、bash・powershellで実行したコマンドとその終了コードを、完了した順に集める。
3. 変更したファイルごとに、必要なテストを決める（[判定の範囲](#判定の範囲)を参照）。
4. 最後に変更した後で、必要なテストを含むコマンドが終了コード0で終わっていなければ、未確認とする。
5. 未確認のものがあれば、`{"decision":"block","reason":"..."}` を標準出力し、テストを実行して失敗を直すか、実行できない場合は最終応答でその旨を明記するよう求める。
6. 差し戻す場合、実装を変えたのに同じhookの `README.md` を変えていなければ、更新が要らないか確認するよう理由に添える。

## 判定の範囲

### 必要なテスト
|変更したファイル|必要なテスト（いずれかの成功で可）|
|--|--|
|`helpers/events/<event>/<hook>/` 配下のファイル|`test-<hook>.py`、`test-all-hooks.py`|
|`helpers/utilities/` 配下のファイル|`test-all-hooks.py`|
|`helpers/runner/` 配下のファイル|`test-run-hook.py`、`test-all-hooks.py`|
|`.github/hooks/<event>.json`|`test-hook-configurations.py`、`test-all-hooks.py`|
|`tests/helpers/` 配下のファイル|`test-hook-test-output.py`（一括実行の探索対象外のため）|
|`tests/test-all-hooks.py`・`tests/test-hook-configurations.py`|そのファイル自体|

- パスは `.github/hooks/` からの相対で示す。`test-<hook>.py` がないhookは、作成してから実行するよう求める。
- コマンドにテストファイル名が含まれていれば実行とみなす。`cd` で移動してから実行した場合や、`-v` などを付けた場合も含む。

### 対象外とする変更
- `.md` ファイルの変更（説明だけの変更）
- 削除したhook（フォルダーが残っていないもの）
- 失敗した編集、`view` などの閲覧
- `.github/hooks/` 以外のファイル

### 検出できない変更
- シェルで作成・変更したファイル。git管理ファイルへの書き込みは [block-shell-file-edits](../../preToolUse/block-shell-file-edits/README.md) hookが拒否するため、主に未追跡の新規ファイルが該当する。

## 備考
- 特別なプロンプト指定は不要。
- [verify-completion-claims](../verify-completion-claims/README.md) は「テストが通った」と主張した場合だけ検査する。本hookは主張の有無によらず、hookを変更したらテストの成功を求める。
- hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
- 同じ依頼で差し戻すのは1回だけ。テストを実行できない場合でも停止できるよう、2回目以降の応答は検査しない。
- サブエージェントの停止は対象外とする。依頼の完了を報告するのは親のため。
- 拒否などで実行されなかったコマンドは終了コードが記録されないため、成功とみなさない。
