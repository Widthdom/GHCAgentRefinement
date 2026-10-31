---
name: clean-hook-temp-files
description: 'hookが一時フォルダーに残した状態ファイルとデバッグログを削除するときに使う。'
---

削除は必ず [clean-hook-temp-files.py](./scripts/clean-hook-temp-files.py) で行う（macOS/Linuxは `python3`、Windowsは `python`）。別の方法は使用しないこと。

1. `--dry-run` 付きで実行する。0件なら報告して終了する。
2. 結果をユーザーに示し、削除してよいか確認する。
3. 承認された場合は `--dry-run` なしで実行し、結果を報告する。
