# .github/hooks/tests README

## test-all-hooks
- [test-all-hooks.py](./test-all-hooks.py) は `.github/hooks/helpers` 配下のすべての `test-*.py` を実行する。
- 各テストファイルは `unittest` 形式で進捗、実行件数、実行時間、結果を標準エラーに出力する。
- 最後の `Passed all <件数> hook test files.` は成功したテストファイル数を表し、続けて `OK` を出力する。失敗したファイルや起動失敗がある場合は理由と `NG` を標準エラーに出力し、終了コード2を返す。

## test-hook-configurations
- [test-hook-configurations.py](./test-hook-configurations.py) は `.github/hooks` 直下のhook設定JSONの構文と基本構造を検証する。
- 各設定ファイルを `subTest` で検証する。

## unittestの出力
- [hook_test_output.py](./helpers/hook_test_output.py) の `run_tests()` は `unittest.main()` の代わりに使う。標準の進捗・実行時間・`OK`・失敗詳細を維持し、失敗時は `FAILED (...)` に続けて `NG` を出力する。成功時は終了コード0、失敗時は1を返す。
- `Ran` の件数はテストメソッド数（スキップを含む）を表す。複数入力や連続した状態変化は同じメソッド内で検証し、`subTest` は件数に加算しない。
- スキップは `s` とサマリーの `skipped` に表示される。個別ファイルに `-v` を付けるとテスト名やスキップ理由を確認できる。`-f`（最初の失敗で停止）やテストメソッド指定も利用できる。
- [test-hook-test-output.py](./helpers/test-hook-test-output.py) は共通出力の逐次進捗・成功・失敗・例外・スキップ・予期しない成功・サブテスト失敗・テスト選択、一括実行の成功・失敗・起動失敗・テスト未検出を検証する。一括hookテストの探索対象外のため、変更時は Windowsでは `python .github\hooks\tests\helpers\test-hook-test-output.py`、macOS/Linuxでは `python3 .github/hooks/tests/helpers/test-hook-test-output.py` で別途実行する。
