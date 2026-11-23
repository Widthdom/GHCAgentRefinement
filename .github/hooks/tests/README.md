# hook tests README

## test_all_hooks
- [test_all_hooks.py](./test_all_hooks.py) は `.github/hooks/helpers` 配下のすべての `test_*.py` を、リポジトリルートをカレントディレクトリとして実行する。実行中のファイルは、リポジトリルート相対の `/` 区切りのパスで表示する。
- 各テストファイルは `unittest` 形式で進捗、実行件数、実行時間、結果を標準エラーに出力する。
- 最後の `Passed all <件数> hook test files.` は成功したテストファイル数を表し、続けて `OK` を出力する。失敗したファイルや起動失敗がある場合は理由と `NG` を標準エラーに出力し、終了コード2を返す。1ファイルの実行が300秒を超えた場合は、そのファイルを失敗として打ち切り、残りのファイルを実行する。

## test_hook_configurations
- [test_hook_configurations.py](./test_hook_configurations.py) は `.github/hooks` 直下のhook設定JSONを一括して検証する。
  - 構文と基本構造（`version` が1、ファイル名のイベントだけを持つ、`type` が `command` の要素の配列）
  - 起動コマンド（hookランナーに実行スクリプトを渡す。`block_commands`・`check_python` は `.sh`・`.cmd` を直接起動する）と、実行スクリプトの存在
  - `matcher` が正規表現として有効で、`timeoutSec` が正の整数であること
  - 要素が実行スクリプトのパスの昇順に並んでいること
  - `helpers/events`・`helpers/-repo-events` 配下のhookのフォルダーと定義が1対1に対応すること
- フォルダーの照合ではgitの管理対象かどうかを見ない。ただし、hookのフォルダーに `__pycache__` 以外の項目がない場合は照合から除く。削除・改名したhookのフォルダーにキャッシュだけが残っていても、定義漏れとして検出しない。未追跡の新規hookは、実ファイルがあれば引き続き検出する。
- 個別hookのテストは、[hook_entry.py](./helpers/hook_entry.py) を `import` で読み込み、`get_hook_entry()` で自身の定義の要素を取得して、`matcher` などhook固有の設定だけを検証する。

## 一時フォルダーを使うテスト
- [temporary_state_test_case.py](./helpers/temporary_state_test_case.py) の `TemporaryStateTestCase` は、テストごとの一時フォルダーを作り、テストのプロセスの `tempfile.gettempdir()` がそのフォルダーを返すよう差し替える `unittest.TestCase` の基底クラス。状態を読み書きするテストはこれを継承し、`setUp` を上書きする場合は最初に `super().setUp()` を呼ぶ。
  - `temp_directory`：テストごとの一時フォルダー（実体のパス）。
  - `temp_environment`：子プロセスで起動するhookにも同じ一時フォルダーを使わせる環境変数（`TMPDIR`・`TEMP`・`TMP`）。
  - `assert_state_path(get_state_path, state_name, extension='.sqlite3')`：状態の保存先が `<一時フォルダー>/ghc_agent_refinement/hook_states/<状態名>/<セッションUUID><拡張子>` になり、セッションIDの表記揺れを同じ保存先にし、UUIDでない値を `ValueError` にすることを検証する。

## unittestの出力
- [hook_test_output.py](./helpers/hook_test_output.py) の `run_tests()` は `unittest.main()` の代わりに使う。各テストは `import` で読み込む。標準の進捗・実行時間・`OK`・失敗詳細を維持し、失敗時は `FAILED (...)` に続けて `NG` を出力する。成功時は終了コード0、失敗時は1を返す。
- `Ran` の件数はテストメソッド数（スキップを含む）を表す。複数入力や連続した状態変化は同じメソッド内で検証し、`subTest` は件数に加算しない。
- スキップは `s` とサマリーの `skipped` に表示される。個別ファイルに `-v` を付けるとテスト名やスキップ理由を確認できる。`-f`（最初の失敗で停止）やテストメソッド指定も利用できる。
- [test_hook_test_output.py](./helpers/test_hook_test_output.py) は共通出力の逐次進捗・成功・失敗・例外・スキップ・予期しない成功・サブテスト失敗・テスト選択、一括実行の成功・失敗・起動失敗・タイムアウト・テスト未検出を検証する。一括hookテストの探索対象外のため、変更時は Windowsでは `python .github\hooks\tests\helpers\test_hook_test_output.py`、macOS/Linuxでは `python3 .github/hooks/tests/helpers/test_hook_test_output.py` で別途実行する。
