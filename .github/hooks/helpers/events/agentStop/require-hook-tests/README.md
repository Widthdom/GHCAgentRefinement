# require-hook-tests hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
hookの実装・定義・テストを変更したのに、最後の変更の後で必要なテストが成功していなければ、テストを実行するか実行できない旨を明記するよう差し戻す。

## 処理詳細
1. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（[wait-for-agent-stop-event](../../../utilities/wait-for-agent-stop-event/README.md)）。
2. ユーザーの最新の依頼以降について、ファイル編集ツール（`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`）で変更に成功したファイルと、bash・powershellで実行したコマンドとその終了コードを、完了した順に集める。
3. 変更したファイルごとに、必要なテストを決める（[判定の範囲](#判定の範囲)を参照）。
4. 最後に変更した後で、必要なテストを含むコマンドが終了コード0で終わっていなければ、未確認とする。
5. 未確認のものがあれば、`{"decision":"block","reason":"..."}` を標準出力し、テストを実行して失敗を直すか、実行できない場合は最終応答でその旨を明記するよう求める。
6. 差し戻す場合、実装を変えたのに同じhookの `README.md` を変えていなければ、更新が要らないか確認するよう理由に添える。

## 判定の範囲

### 必要なテスト
|変更したファイル|必要なテスト（いずれかの成功で可）|
|--|--|
|`helpers/events/<event>/<hook>/`・`helpers/-repo-events/<event>/<hook>/` 配下のファイル|`test-<hook>.py`、[test-all-hooks.py](../../../../tests/test-all-hooks.py)|
|`helpers/utilities/` 配下のファイル|`test-all-hooks.py`|
|`helpers/runner/` 配下のファイル|[test-run-hook.py](../../../runner/test-run-hook.py)、[test-all-hooks.py](../../../../tests/test-all-hooks.py)|
|`.github/hooks/<event>.json`・`.github/hooks/-repo-<event>.json`|[test-hook-configurations.py](../../../../tests/test-hook-configurations.py)、[test-all-hooks.py](../../../../tests/test-all-hooks.py)|
|`tests/helpers/hook-test-output.py`・`tests/helpers/test-hook-test-output.py`|[test-hook-test-output.py](../../../../tests/helpers/test-hook-test-output.py)（一括実行の探索対象外のため）|
|`tests/helpers/` 配下のその他のファイル|`test-all-hooks.py`（個別のテストが読み込むため）|
|`tests/test-all-hooks.py`・`tests/test-hook-configurations.py`|そのファイル自体|

- パスは [.github/hooks/](../../../../../hooks/) からの相対で示す。`test-<hook>.py` がないhookは、作成してから実行するよう求める。
- コマンドにテストファイル名が含まれていれば実行とみなす。`cd` で移動してから実行した場合や、`-v` などを付けた場合も含む。

## 例外
- `.md` ファイルの変更（説明だけの変更）
- 削除したhook（フォルダーが残っていないもの）
- 失敗した編集、`view` などの閲覧
- [.github/hooks/](../../../../../hooks/) 以外のファイル
- 依頼の区切りは [get-turn-events](../../../utilities/get-turn-events/README.md) の規則に従う。
- サブエージェントの停止は対象外とする。依頼の完了を報告するのは親のため。

## 制約と限界
- シェルで作成・変更したファイルは検出できない。git管理ファイルへの書き込みは [block-shell-file-edits](../../preToolUse/block-shell-file-edits/README.md) hookが拒否するため、主に未追跡の新規ファイルが該当する。
- 同じ依頼で差し戻すのは1回だけ。テストを実行できない場合でも停止できるよう、2回目以降の応答は検査しない。
- 拒否などで実行されなかったコマンドは終了コードが記録されないため、成功とみなさない。

## 備考
- [verify-test-pass-claims](../verify-test-pass-claims/README.md) は「テストが通った」と主張した場合だけ検査する。本hookは主張の有無によらず、hookを変更したらテストの成功を求める。
