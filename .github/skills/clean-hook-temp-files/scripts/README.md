# clean-hook-temp-files skill scripts README

## clean_hook_temp_files.py
hookが一時フォルダーに残した状態ファイルとデバッグログのうち、48時間を超えて更新されていないものを削除する。

```
python3 clean_hook_temp_files.py [--dry-run]
```

- `--dry-run`：削除せず、一時フォルダーのパス、対象ファイルの件数・容量、ディレクトリごとの件数を表示する。
- 指定なし：対象ファイルを削除し、削除した件数・容量と、削除した空ディレクトリの数を表示する。削除できなかったファイルは、パスと理由を標準エラーに表示する。

対象は `<一時フォルダー>/ghc_agent_refinement/` の `hook_states/` と `debug_logs/` の配下。対象の分類と保持期間は、件数を警告する [check_hook_temp_files hook](../../../hooks/helpers/events/sessionStart/check_hook_temp_files/README.md) と共有するため、[hook_temp_files.py](../../../hooks/helpers/events/sessionStart/check_hook_temp_files/hook_temp_files.py) から読み込む。

終了コードは、成功が0、削除できなかったファイルがある場合が2。

`--dry-run` の集計、削除、削除できなかった場合の終了コードは、判定基準を共有する [test_check_hook_temp_files.py](../../../hooks/helpers/events/sessionStart/check_hook_temp_files/test_check_hook_temp_files.py) で検証する。hookの一括テストに含まれる。
